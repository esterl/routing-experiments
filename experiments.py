import itertools
import copy

from configparser import ConfigParser, NoOptionError
from datetime import timedelta as delta
from functools import total_ordering
from heapq import heappush
from time import sleep
#from enum import Enum

import networkx as nx

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
    commands = ConfigParser({'pid':'ps -o pid -C "%(Command)s"'})
    
    _counter = itertools.count()
    
    def __str__(self):
        node = "%s@" % self._command if self.kind == EXEC else ''
        result = "[%s]%s %s%s" % (self.at, self.kind, node, self.target)
        return result
    
    def __init__(self, kind, at, target, _command="", monitors=[]):
        self._id = next(Action._counter)
        self.kind = kind
        self.at = at
        self.target = target
        self._command = _command
        self.pid = None
        self.monitors=monitors
    
    def __eq__(self, other):
        return self._id == other._id
    
    def __lt__(self, other):
        return ((self.at, self._id) < (other.at, other._id))
    
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


class Topology(nx.Graph):
    pass


class Experiment(object):
    
    def __str__(self):
        return '\n'.join([ str(a) for a in self.actions ])
    
    def __init__(self,topology=Topology()):
        self.topology = topology
        self.actions = []
        self._last = delta(0)
    
    def boot(self, nodes=None, at=None, delay=delta(0), inter_delay=delta(0)):
        self.schedule(BOOT, nodes, at, delay, inter_delay)
    
    def halt(self, nodes=None, at=None, delay=delta(0), inter_delay=delta(0)):
        self.schedule(HALT, nodes, at, delay, inter_delay)
    
    def apply_dynamic_links(self, at=None, delay=delta(0), inter_delay=delta(0)):
        self.schedule(START, NETWORK, at, delay, inter_delay)
    
    def execute(self, command, nodes=None, at=None, delay=delta(0), 
                inter_delay=delta(0), monitors=[]):
        self.schedule(EXEC, nodes, at, delay, inter_delay, command, monitors)
    
    def start(self, at=None, delay=delta(0)):
        self.schedule(START, EXPERIMENT, at, delay, delta(0))
    
    def stop(self, at=None, delay=delta(0), inter_delay=delta(0)):
        self.schedule(STOP, EXPERIMENT, at, delay, inter_delay)
    
    def monitor(self, monitors, at=None, delay=delta(0), inter_delay=delta(0)):
        self.schedule(MON, EXPERIMENT, at, delay, inter_delay, monitors=monitors)
    
    def schedule(self, action, target, at, delay, inter_delay, command="", monitors=[]):
        if target is None:
            target = self.topology.nodes()
        if at is None:
            at = self._last
        if inter_delay != delta(0) and isiterable(target):
            for t in target:
                self.schedule(action, t, at, delay, inter_delay, command, monitors)
                at = None
                delay = inter_delay
                monitors = copy.deepcopy(monitors)
        else:
            if callable(delay):
                delay = delay()
            self._last = at + delay
            entry = Action(action, self._last, target, command, monitors=monitors)
            heappush(self.actions, entry)
