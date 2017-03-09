#! /usr/bin/python

import logging
logg = logging.getLogger("systemctl")

import re
import fnmatch
import shlex
import collections
import ConfigParser
import errno
import os
import sys
import subprocess

# http://stackoverflow.com/questions/568271/how-to-check-if-there-exists-a-process-with-a-given-pid
def pid_exists(pid):
    """Check whether pid exists in the current process table.
    UNIX only.
    """
    if pid is None:
        return False
    if pid < 0:
        return False
    if pid == 0:
        # According to "man 2 kill" PID 0 refers to every process
        # in the process group of the calling process.
        # On certain systems 0 is a valid PID but we have no way
        # to know that in a portable fashion.
        raise ValueError('invalid PID 0')
    try:
        os.kill(pid, 0)
    except OSError as err:
        if err.errno == errno.ESRCH:
            # ESRCH == No such process
            return False
        elif err.errno == errno.EPERM:
            # EPERM clearly means there's a process to deny access to
            return True
        else:
            # According to "man 2 kill" possible error values are
            # (EINVAL, EPERM, ESRCH)
            raise
    else:
        return True

class UnitConfig:
    def __init__(self, defaults=None, dict_type=None, allow_no_value=False):
        self._defaults = defaults or {}
        self._dict_type = dict_type or collections.OrderedDict
        self._allow_no_value = allow_no_value
        self._dict = self._dict_type()
        self._files = []
    def defaults(self):
        return self.defaults
    def sections(self):
        return self._dict.keys()
    def add_section(self, section):
        if section not in self._dict:
            self._dict[section] = self._dict_type()
    def has_section(self, section):
        return section in self._dict
    def has_option(self, section, option):
        if section in self._dict:
            return False
        return option in self._dict[section]
    def set(self, section, option, value):
        if section not in self._dict:
            self._dict[section] = self._dict_type()
        if option not in self._dict[section]:
            self._dict[section][option] = [ value ]
        else:
            self._dict[section][option].append(value)
    def get(self, section, option, default = None):
        if section not in self._dict:
            if default is not None:
                return default
            if self._allow_no_value:
                return none
            raise AttributeError("section {} does not exit".format(section))
        if option not in self._dict[section]:
            if default is not None:
                return default
            if self._allow_no_value:
                return none
            raise AttributeError("option {} in {} does not exit".format(option, section))
        return self._dict[section][option][0]
    def getlist(self, section, option):
        if section not in self._dict:
            raise AttributeError("section {} does not exit".format(section))
        if option not in self._dict[section]:
            if self._allow_no_value:
                return none
            raise AttributeError("option {} in {} does not exit".format(option, section))
        return self._dict[section][option]
    def filename(self):
        if self._files:
            return self._files[-1]
        return None
    def read(self, filename):
        initscript = False
        initinfo = False
        section = None
        if os.path.isfile(filename):
            self._files.append(filename)
        for orig_line in open(filename):
            line = orig_line.strip()
            if line.startswith("#"):
                continue
            if line.startswith("["):
                x = line.find("]")
                if x > 0:
                    section = line[1:x]
                    self.add_section(section)
                continue
            m = re.match(r"(\w+)=(.*)", line)
            if m:
                self.set(section, m.group(1), m.group(2).strip())
    def sysv_read(self, filename):
        initscript = False
        initinfo = False
        section = None
        if os.path.isfile(filename):
            self._files.append(filename)
        for orig_line in open(filename):
            line = orig_line.strip()
            if line.startswith("#"):
                if " BEGIN INIT INFO": 
                     initinfo = True
                     section = "Unit"
                if " END INIT INFO": 
                     initinfo = False
                if initinfo:
                    m = re.match(r"^\S+\s*(\w+):(.*)", line)
                    if m:
                        self.set(section, m.group(1), m.group(2).strip())
                continue

UnitParser = ConfigParser.RawConfigParser
UnitParser = UnitConfig


class Systemctl:
    def __init__(self):
        self.systemfolder1 = "/usr/lib/systemd/system"
        self.systemfolder2 = "/etc/systemd/system"
        self.sysv_folder1 = "/etc/init.d"
        self.sysv_folder2 = "/var/run/init.d"
        self.waitprocfile = 100
        self.waitkillproc = 10
    def unit_file(self, module):
        for systemfolder in (self.systemfolder1, self.systemfolder2):
            path = os.path.join(systemfolder, module)
            if os.path.isfile(path):
                return path
        return None
    def read_unit(self, module):
        path = self.unit_file(module)
        if not path:
            logg.warning("unit file not found: %s", module)
            raise Exception("unit file not found")
        unit = UnitParser()
        unit.read(path)
        return unit
    def read_sysv_unit(self, module):
        path = self.unit_file(module)
        if not path:
            logg.warning("unit file not found: %s", module)
            raise Exception("unit file not found")
        unit = UnitParser()
        unit.sysv_read(path)
        return unit
    def try_read_unit(self, module):
        try: 
            return self.read_unit(module)
        except Exception, e: 
            logg.debug("read unit '%s': %s", module, e)
    def units(self, modules, suffix=".service"):
        if isinstance(modules, basestring):
            modules = [ modules ]
        for folder in (self.systemfolder1, self.systemfolder2):
            if not folder or not os.path.isdir(folder):
                continue
            for item in os.listdir(folder):
                if not modules: 
                    yield item
                elif [ module for module in modules if fnmatch.fnmatch(item, module) ]:
                    yield item
                elif item == module+".service":
                    yield item
    def sysv_units(self, modules):
        if isinstance(modules, basestring):
            modules = [ modules ]
        for folder in (self.sysv_folder1, self.sysv_folder2):
            if not folder or not os.path.isdir(folder):
                continue
            for item in os.listdir(folder):
                if not modules: 
                    yield item
                elif [ module for module in modules if fnmatch.fnmatch(item, module) ]:
                    yield item
    def list_units_of_host(self, *modules):
        result = {}
        description = {}
        for unit in self.units(modules):
            result[unit] = None
            description[unit] = ""
            try: 
                conf = self.try_read_unit(unit)
                result[unit] = conf
                description[unit] = self.get_description_from(conf)
            except Exception, e:
                logg.warning("list-units: %s", e)
        return [ (unit, result[unit] and "loaded" or "", description[unit]) for unit in sorted(result) ]
    def get_description_from(self, conf, default = None):
        if not conf: return defualt or ""
        return conf.get("Unit", "Description")
    def write_pid_file(self, pid_file, pid):
        dirpath = os.path.dirname(os.path.abspath(pid_file))
        if not os.path.isdir(dirpath):
            os.makedirs(dirpath)
        with open(pid_file, "w") as f:
            f.write(pid+"\n")
    def pid_exists(self, pid):
        # return os.path.isdir("/proc/%s" % pid)
        return pid_exists(pid)
    def wait_pid_file(self, pid_file):
        dirpath = os.path.dirname(os.path.abspath(pid_file))
        for x in xrange(self.waitprocfile):
            if not os.path.isdir(dirpath):
                self.sleep(1)
                continue
            pid = self.read_pid_file(pid_file)
            if not pid:
                continue
            if not pid_exists(pid):
                continue
            return pid
        return None
    def default_pid_file(self, unit):
        return "/var/run/%s.pid" % unit
    def read_env_file(self, env_file):
        if env_file.startswith("-"):
            env_file = env_file[1:]
            if not os.path.isfile(env_file):
                return
        try:
            for real_line in open(env_file):
                line = real_line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"([\w_]+)[=]'([^']*)'", line)
                if m:
                    yield m.group(1), m.group(2)
                    continue
                m = re.match(r'([\w_]+)[=]"([^"]*)"', line)
                if m:
                    yield m.group(1), m.group(2)
                    continue
                m = re.match(r'([\w_]+)[=](.*)', line)
                if m:
                    yield m.group(1), m.group(2)
                    continue
        except Exception, e:
            logg.info("while reading %s: %s", env_file, e)
    def start_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        if units:
            for unit in units:
                self.do_start(unit)
        else:
            for unit in self.sysv_units(modules):
                self.sysv_start(unit)
    def do_start(self, unit):
        conf = self.read_unit(unit)
        self.do_start_from(conf)
    def sysv_start(self, unit):
        conf = self.read_sysv_unit(unit)
        self.do_start_from(conf)
    def do_start(self, unit):
        conf = self.read_sysv_unit(unit)
        conf.set("Service", "Type", "sysv")
        self.do_start_from(conf)
    def do_start_from(self, conf):
        if not conf: return
        runs = conf.get("Service", "Type", "simple").lower()
        if True:
            for cmd in conf.getlist("Service", "ExecStartPre"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
        if runs in [ "sysv" ]:
            if True:
                 cmd = conf.filename()
                 logg.info("start %s", cmd)
                 args = [ cmd, "start" ]
                 pid = os.spawnl(os.P_WAIT, *args)
        elif runs in [ "simple" ]:
            env = os.environ.copy()
            for env_file in conf.getlist("Service", "EnvironmentFile"):
                for name, value in self.read_env_file(env_file):
                    env[name] = value
            for cmd in conf.getlist("Service", "ExecStart"):
                 logg.info("start %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 pid = os.spawnle(os.P_NOWAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.write_pid_file(pid_file, pid)
        elif runs in [ "forking" ]:
            env = os.environ.copy()
            for env_file in conf.getlist("Service", "EnvironmentFile"):
                for name, value in self.read_env_file(env_file):
                    env[name] = value
            for cmd in conf.getlist("Service", "ExecStart"):
                 logg.info("start %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 sta = os.spawnle(os.P_WAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.wait_pid_file(pid_file)
        else:
            logg.error("unsupported run type '%s'", runs)
            raise Exception("unsupported run type")
        if True:
            for cmd in conf.getlist("Service", "ExecStartPost"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
    def read_pid_file(self, pid_file, default = None):
        pid = default
        if not pid_file:
            return default
        if not os.path.isfile(pid_file):
            return default
        try:
            for line in open(pid_file):
                if line.strip(): 
                    pid = int(line.strip())
                    break
        except:
            logg.warning("bad read of pid file '%s'", pid_file)
        return pid
    def kill_pid(self, pid):
        for x in xrange(self.waitkillproc):
            os.kill(pid, signal.SIGTERM)
            if not self.pid_exists(pid):
                break
            self.sleep(1)
            if not self.pid_exists(pid):
                break
        for x in xrange(self.waitkillproc):
            if not self.pid_exists(pid):
                break
            os.kill(pid, signal.SIGKILL)
            self.sleep(1)
    def stop_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        if units:
            for unit in units:
                self.do_stop(unit)
        else:
            for unit in self.sysv_units(modules):
                self.sysv_stop(unit)
    def do_stop(self, unit):
        conf = self.read_unit(unit)
        self.do_stop_from(conf)
    def sysv_stop(self, unit):
        conf = self.read_sysv_unit(unit)
        conf.set("Service", "Type", "sysv")
        self.do_stop_from(conf)
    def do_stop_from(self, conf):
        if not conf: return
        runs = conf.get("Service", "Type", "simple").lower()
        if True:
            for cmd in conf.getlist("Service", "ExecStopPre"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
        if runs in [ "sysv" ]:
            if True:
                 cmd = conf.filename()
                 logg.info("stop %s", cmd)
                 args = [ cmd, "stop" ]
                 pid = os.spawnl(os.P_WAIT, *args)
        elif not conf.getlist("Service", "ExecStop"):
            if True:
                 pid_file = self.get_pid_file_from(conf)
                 pid = self.read_pid_file(pid_file)
                 logg.info("stop %s (%s)", pid, pid_file)
                 self.kill_pid(pid)
                 if os.path.isfile(pid_file):
                     os.remove(pid_file)
        elif runs in [ "simple" ]:
            env = os.environ.copy()
            for env_file in conf.getlist("Service", "EnvironmentFile"):
                for name, value in self.read_env_file(env_file):
                    env[name] = value
            for cmd in conf.getlist("Service", "ExecStop"):
                 logg.info("stop %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 pid = os.spawnle(os.P_NOWAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.write_pid_file(pid_file, pid)
        elif runs in [ "forking" ]:
            env = os.environ.copy()
            for env_file in conf.getlist("Service", "EnvironmentFile"):
                for name, value in self.read_env_file(env_file):
                    env[name] = value
            for cmd in conf.getlist("Service", "ExecStop"):
                 logg.info("stop %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 sta = os.spawnle(os.P_WAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.wait_pid_file(pid_file)
        else:
            logg.error("unsupported run type '%s'", runs)
            raise Exception("unsupported run type")
        if True:
            for cmd in conf.getlist("Service", "ExecStopPost"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
    def reload_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        if units:
            for unit in units:
                self.do_reload(unit)
        else:
            for unit in self.sysv_units(modules):
                self.sysv_restart(unit)
    def do_reload(self, unit):
        conf = self.read_unit(unit)
        self.do_reload_from(conf)
    def sysv_reload(self, unit):
        conf = self.read_sysv_unit(unit)
        conf.set("Service", "Type", "sysv")
        self.do_reload_from(conf)
    def do_reload_from(self, conf):
        if not conf: return
        runs = conf.get("Service", "Type", "simple").lower()
        if True:
            for cmd in conf.getlist("Service", "ExecReloadPre"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
        if runs in [ "sysv" ]:
            if True:
                 cmd = conf.filename()
                 logg.info("reload %s", cmd)
                 args = [ cmd, "reload" ]
                 pid = os.spawnl(os.P_WAIT, *args)
        elif runs in [ "simple" ]:
            env = os.environ.copy()
            for env_file in conf.getlist("Service", "EnvironmentFile"):
                for name, value in self.read_env_file(env_file):
                    env[name] = value
            for cmd in conf.getlist("Service", "ExecReload"):
                 logg.info("start %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 pid = os.spawnle(os.P_NOWAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.write_pid_file(pid_file, pid)
        elif runs in [ "forking" ]:
            env = os.environ.copy()
            for env_file in conf.getlist("Service", "EnvironmentFile"):
                for name, value in self.read_env_file(env_file):
                    env[name] = value
            for cmd in conf.getlist("Service", "ExecReload"):
                 logg.info("start %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 sta = os.spawnle(os.P_WAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.wait_pid_file(pid_file)
        else:
            logg.error("unsupported run type '%s'", runs)
            raise Exception("unsupported run type")
        if True:
            for cmd in conf.getlist("Service", "ExecReloadPost"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
    def restart_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        if units:
            for unit in units:
                self.do_restart(unit)
        else:
            for unit in self.sysv_units(modules):
                self.sysv_restart(unit)
    def do_restart(self, unit):
        conf = self.read_unit(unit)
        self.do_restart_from(conf)
    def sysv_restart(self, unit):
        conf = self.read_sysv_unit(unit)
        conf.set("Service", "Type", "sysv")
        self.do_restart_from(conf)
    def do_restart_from(self, conf):
        if not conf: return
        runs = conf.get("Service", "Type", "simple").lower()
        if True:
            for cmd in conf.getlist("Service", "ExecRestartPre"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
        env = os.environ.copy()
        for env_file in conf.getlist("Service", "EnvironmentFile"):
            for name, value in self.read_env_file(env_file):
                env[name] = value
        if runs in [ "sysv" ]:
            if True:
                 cmd = conf.filename()
                 logg.info("reload %s", cmd)
                 args = [ cmd, "restart" ]
                 pid = os.spawnl(os.P_WAIT, *args)
        elif not conf.getlist("Service", "ExceRestart"):
            self.do_stop_from(conf)
            self.do_start_from(conf)
        elif runs in [ "simple" ]:
            for cmd in conf.getlist("Service", "ExecRestart"):
                 logg.info("start %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 pid = os.spawnle(os.P_NOWAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.write_pid_file(pid_file, pid)
        elif runs in [ "forking" ]:
            for cmd in conf.getlist("Service", "ExecRestart"):
                 logg.info("start %s", cmd)
                 args = shlex.split(cmd)
                 args.append(env)
                 sta = os.spawnle(os.P_WAIT, *args)
                 pid_file = self.get_pid_file_from(conf)
                 self.wait_pid_file(pid_file)
        else:
            logg.error("unsupported run type '%s'", runs)
            raise Exception("unsupported run type")
        if True:
            for cmd in conf.getlist("Service", "ExecRestartPost"):
                if cmd.startswith("-"):
                    cmd = cmd[1:]
                    subprocess.call(cmd, shell=True)
                else:
                    subprocess.check_call(cmd, shell=True)
    def get_pid_file(self, unit):
        conf = self.read_unit(unit)
        return self.get_pid_file_from(conf)
    def get_pid_file_from(self, conf, default = None):
        if not conf: return default
        unit = os.path.basename(conf.filename())
        if default is None:
            default = self.default_pid_file(unit)
        return conf.get("Service", "PIDFile", default)
    def try_restart_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.try_restart(unit)
    def try_restart(unit):
        conf = self.read_unit(unit)
        if self.is_active_from(conf):
            self.do_restart_from(conf)
    def reload_or_restart_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.reload_or_start(unit)
    def reload_or_restart(unit):
        conf = self.read_unit(unit)
        if not self.is_active_from(conf):
            self.do_start_from(conf)
        elif conf.getlist("Service", "ExecReload"):
            self.do_reload_from(conf)
        else:
            self.do_restart_from(conf)
    def reload_or_try_restart_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.reload_or_try_restart(unit)
    def reload_or_try_restart(unit):
        conf = self.read_unit(unit)
        if not self.is_active_from(conf):
            return
        if conf.getlist("Service", "ExecReload"):
            self.do_reload_from(conf)
        else:
            self.do_restart_from(conf)
    def kill_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.do_kill(unit)
    def do_kill(self, unit):
        conf = self.read_unit(unit)
        self.do_kill_from(conf)
    def do_kill_from(self, conf):
        if not conf: return
        pid_file = self.get_pid_file_from(conf)
        pid = self.read_pid_file(pid_file)
        self.kill_pid(pid)
    def is_active_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        result = False
        for unit in units:
            if self.is_active(unit):
                result = True
        return result
    def is_active(self, unit):
        conf = self.try_read_unit(unit)
        if not conf:
            logg.warning("no such unit '%s'", unit)
        return self.is_active_from(conf)
    def is_active_from(self, conf):
        if not conf: return False
        pid_file = self.get_pid_file_from(conf)
        pid = self.read_pid_file(pid_file)
        exists = self.pid_exists(pid)
        return exists
    def active_from(self, conf):
        if not conf: return False
        pid_file = self.get_pid_file_from(conf)
        pid = self.read_pid_file(pid_file)
        exists = self.pid_exists(pid)
        if not exists: return "dead"
        return "PID %s" % pid
    def is_failed_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        result = False
        for unit in units:
            if self.is_failed(unit):
                result = True
        return result
    def is_failed(self, unit):
        conf = self.try_read_unit(unit)
        if not conf:
            logg.warning("no such unit '%s'", unit)
        return self.is_failed_from(conf)
    def is_failed_from(self, conf):
        if not conf: return True
        pid_file = self.get_pid_file_from(conf)
        pid = self.read_pid_file(pid_file)
        return not self.pid_exists(pid)
    def status_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.do_status(unit)
    def do_status(self, unit):
        conf = self.try_read_unit(unit)
        print unit, "-", self.get_description_from(conf)
        if conf:
            print "    Loaded: loaded ({}, {})".format( conf.filename(), self.enabled_from(conf) )
        else:
            print "    Loaded: failed"
            return
        if self.is_active_from(conf):
            print "    Active: active ({})".format(self.active_from(conf))
        else:
            print "    Active: inactive ({})".format(self.active_from(conf))
    def shows_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.do_show(unit)
    def cat_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.do_cat(unit)
    def wanted_from(self, conf, default = None):
        if not conf: return default
        return conf.get("Install", "WantedBy", default)
    def enablefolder(self, wanted = None):
        if not wanted: return None
        if not wanted.endswith(".wants"):
            wanted = wanted + ".wants"
        return "/etc/systemd/system/" + wanted
    def enable_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.do_enable(unit)
    def do_enable(self, unit):
        unit_file = self.unit_file(unit)
        wanted = self.wanted_from(self.try_read_unit(unit))
        folder = self.enablefolder(wanted)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        target = os.path.join(folder, os.path.basename(unit_file))
        print "ln -s '%s' '%s'" % (unit_file, target)
        return os.symlink(unit_file, target)
    def disable_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        for unit in units:
            self.do_disable(unit)
    def do_disable(self, unit):
        unit_file = self.unit_file(unit)
        wanted = self.wanted_from(self.try_read_unit(unit))
        folder = self.enablefolder(wanted)
        if not os.path.isdir(folder):
            return False
        target = os.path.join(folder, os.path.basename(unit_file))
        if os.path.isfile(target):
            print "rm '%s'" % (target)
            os.remove(target)
    def is_enabled_unit(self, *modules):
        units = {}
        for unit in self.units(modules):
            units[unit] = 1
        result = False
        for unit in units:
            if self.is_enabled(unit):
               result = True
        return result
    def is_enabled(self, unit):
        unit_file = self.unit_file(unit)
        wanted = self.wanted_from(self.try_read_unit(unit))
        folder = self.enablefolder(wanted)
        if not wanted:
            return True
        target = os.path.join(folder, os.path.basename(unit_file))
        if os.path.isfile(target):
            return True
        return False
    def enabled_from(self, conf):
        unit_file = conf.filename()
        wanted = self.wanted_from(conf)
        folder = self.enablefolder(wanted)
        if not wanted:
            return "static"
        target = os.path.join(folder, os.path.basename(unit_file))
        if os.path.isfile(target):
            return "enabled"
        return "disabled"

if __name__ == "__main__":
    import optparse
    _o = optparse.OptionParser("%prog [options] command [name...]")
    _o.add_option("-t","--type", metavar="NAMES")
    _o.add_option("--state", metavar="STATES")
    _o.add_option("-p", "--property", metavar="PROPERTIES")
    _o.add_option("-a", "--all", action="store_true")
    _o.add_option("--reverse", action="store_true")
    _o.add_option("--after", action="store_true")
    _o.add_option("--before", action="store_true")
    _o.add_option("-l","--full", action="store_true")
    _o.add_option("--show-types", action="store_true")
    _o.add_option("--job-mode", metavar="JOBTYPE")    
    _o.add_option("-i","--ignore-inhibitors", action="store_true")
    _o.add_option("-q","--quiet", action="store_true")
    _o.add_option("--no-block", action="store_true")
    _o.add_option("--no-legend", action="store_true")
    _o.add_option("--user", action="store_true")
    _o.add_option("--system", action="store_true")
    _o.add_option("--no-wall", action="store_true")
    _o.add_option("--global", action="store_true")
    _o.add_option("--no-reload", action="store_true")
    _o.add_option("--no-ask-password", action="store_true")
    _o.add_option("--kill-who", metavar="ALL")
    _o.add_option("-s", "--signal", metavar="KILLSIG")
    _o.add_option("--force", action="store_true")
    _o.add_option("--root", metavar="PATH")
    _o.add_option("--runtime", metavar="PROPERTY")
    _o.add_option("-n","--lines", metavar="NUMBER")
    _o.add_option("-o","--output", metavar="SHORT")
    _o.add_option("--plain", action="store_true")
    _o.add_option("-H","--host", metavar="NAME")
    _o.add_option("-M","--machine", metavar="CONTAINER")
    _o.add_option("--no-pager", action="store_true")
    _o.add_option("--version", action="store_true")
    _o.add_option("-v","--verbose", action="count", default=0)
    opt, args = _o.parse_args()
    logging.basicConfig(level = max(0, logging.FATAL - 10 * opt.verbose))
    logg.setLevel(max(0, logging.ERROR - 10 * opt.verbose))
    #
    if not args: args = [ "list-units" ]
    command = args[0]
    modules = args[1:]
    systemctl = Systemctl()
    found = False
    for suffix in [ "_unit", "_of_units", "_host", "_of_host" ]:
        command_name = command.replace("-","_").replace(".","_")+suffix
        command_func = getattr(systemctl, command_name, None)
        if callable(command_func):
            found = True
            result = command_func(*modules)
            break
    if not found:
        logg.error("no method for '%s'", command)
        sys.exit(1)
    if result is None:
        sys.exit(0)
    elif result is True:
        sys.exit(0)
    elif result is False:
        sys.exit(1)
    elif isinstance(result, basestring):
        print result
    elif isinstance(result, list):
        for element in result:
            if isinstance(element, tuple):
                print "\t".join(element)
            else:
                print element
    else:
        logg.warning("unknown result type %s", str(type(result)))

