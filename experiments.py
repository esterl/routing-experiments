import copy
import itertools
import logging
import os.path

from configparser import ConfigParser, NoOptionError
from datetime import timedelta as delta
from functools import total_ordering
from time import sleep
#from enum import Enum

from utils import isiterable


# TODO use enum
#Type = Enum('Type', 'Boot Halt Exec Start Stop')
BOOT = 'boot'
HALT = 'halt'
EXEC = 'exec'
START = 'start'
STOP = 'stop'
MON = 'monitor'
NETWORK = 'network'
EXPERIMENT = 'experiment'


@total_ordering
class Action(object):
    commands = ConfigParser({'pid': 'ps -o pid -C "%(Command)s"'})
    _counter = itertools.count()
    
    def __str__(self):
        node = '%s@' % self._command if self.kind == EXEC else ''
        result = '[%s]%s %s%s' % (self.at, self.kind, node, self.target)
        if self.monitors:
            monitors = [monitor.basename for monitor in self.monitors]
            result += '(Monitors: %s)' % ', '.join(monitors)
        return result
    
    def __init__(self, kind, at, target, _command="", monitors=[]):
        self._id = next(Action._counter)
        self.kind = kind
        self.at = at
        self.target = target
        self._command = _command
        self.pid = None
        self.monitors = monitors
    
    def __eq__(self, other):
        return self._id == other._id
    
    def __lt__(self, other):
        return (self.at, self._id) < (other.at, other._id)
    
    @staticmethod
    def load_config(filename):
        Action.commands.read(filename)
    
    def get_command(self):
        return Action.commands.get(self._command, 'command')
    
    def get(self, key):
        try:
            return Action.commands.get(self._command, key)
        except NoOptionError:
            return None
    
    def get_pid_cmd(self):
        return Action.commands.get(self._command, 'pid')
    
    def get_id(self):
        return self._id
    
    def modify(self, new_command):
        if self.kind != EXEC:
            logging.error('Only execute actions can be changed')
        else:
            self._command = new_command


class Experiment(object):
    
    def __str__(self):
        self.actions.sort()
        return '\n'.join([ str(a) for a in self.actions ])
    
    def __init__(self, basename, topology):
        self.topology = topology
        self.actions = []
        self._last = delta(0)
        self.set_name(basename)
    
    def set_name(self, basename):
        self.path = os.path.dirname(os.path.abspath(basename))
        self.name = os.path.basename(basename)
        if not os.path.isdir(basename):
            os.mkdir(basename)
        for action in self.actions:
            for monitor in action.monitors:
                monitor.set_filename(self.path, self.name)
    
    def boot(self, nodes=None, at=None, delay=delta(0), inter_delay=delta(0)):
        return self.schedule(BOOT, nodes, at, delay, inter_delay)
    
    def halt(self, nodes=None, at=None, delay=delta(0), inter_delay=delta(0)):
        return self.schedule(HALT, nodes, at, delay, inter_delay)
    
    def apply_dynamic_links(self, at=None, delay=delta(0), inter_delay=delta(0)):
        return self.schedule(START, NETWORK, at, delay, inter_delay)
    
    def execute(self, command, nodes=None, at=None, delay=delta(0), inter_delay=delta(0), monitors=[]):
        return self.schedule(EXEC, nodes, at, delay, inter_delay, command, monitors)
    
    def start(self, at=None, delay=delta(0)):
        return self.schedule(START, EXPERIMENT, at, delay, delta(0))
    
    def stop(self, at=None, delay=delta(0), inter_delay=delta(0)):
        return self.schedule(STOP, EXPERIMENT, at, delay, inter_delay)
    
    def monitor(self, monitors, at=None, delay=delta(0), inter_delay=delta(0)):
        return self.schedule(MON, EXPERIMENT, at, delay, inter_delay, monitors=monitors)
    
    def schedule(self, action, target, at, delay, inter_delay, command="", monitors=[]):
        if not isiterable(monitors):
            monitors = [monitors]
        for monitor in monitors:
            if target is not None and not isiterable(target):
                monitor.add_tag(str(target))
            monitor.set_filename(self.path, self.name)
            monitor.target = target
        if target is None:
            target = self.topology.vs.indices
        if at is None:
            at = self._last
        if inter_delay != delta(0) and isiterable(target):
            result = []
            for t in target:
                monitors_copy = copy.deepcopy(monitors)
                res = self.schedule(action, t, at, delay, inter_delay, command, monitors_copy)
                result.append(res)
                at = None
                delay = inter_delay
            return result
        else:
            if callable(delay):
                delay = delta(seconds=delay())
            at += delay
            if at > self._last:
                self._last = at  
            entry = Action(action, at, target, command, monitors=monitors)
            self.actions.append(entry)
            return entry.get_id()
    
    def modify_command(self, action_ids, new_command):
        if not isiterable(action_ids):
            action_ids = [action_ids]
        for action in self.actions:
            if action.get_id() in action_ids:
                action.modify(new_command)
    
    def done(self):
        # Save experiment topology:
        topo_name = os.path.join(self.path, self.name, '%s_topology' % self.name)
        self.topology.save(topo_name, format='graphml')
        # Retrieve and save the data of every monitor
        self.global_data = dict()
        self.target_data = dict()
        # Retrieve data of every monitor
        for action in self.actions:
            for monitor in action.monitors:
                global_data = monitor.get_global_data()
                if global_data:
                    for key,value in global_data.items():
                        self.global_data[key] = str(value)
                target_data = monitor.get_target_data()
                if not isinstance(target_data,list):
                    target_data = [target_data]
                for t_d in target_data:
                    (target, data) = t_d
                    if target:
                        if target in self.target_data:
                            self.target_data[target].update(data)
                        else:
                            self.target_data[target] = data
        # Save global_data and target_data
        # Global data
        global_filename = os.path.join(self.path, self.name, '%s_global' % self.name)
        self.global_data['graph_file'] = topo_name
        with open(global_filename, 'w') as f:
            f.write('"')
            f.write('","'.join(self.global_data.keys()))
            f.write('"\n"')
            f.write('","'.join(self.global_data.values()))
            f.write('"\n')
        target_filename = os.path.join(self.path, self.name, '%s_target' % self.name)
        # Target data
        # Get all the headers:
        headers = set()
        for target_dict in self.target_data.values():
            headers |= target_dict.keys()
        with open(target_filename, 'w') as f:
            f.write('"target","')
            f.write('","'.join(headers))
            f.write('"\n')
            for (target, target_dict) in self.target_data.items():
                data = [str(target)]
                data += [ str(target_dict.get(key, '<NA>')) for key in headers ]
                f.write('"')
                f.write('","'.join(data))
                f.write('"\n')

