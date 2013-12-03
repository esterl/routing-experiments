#!/usr/bin/python3

from datetime import timedelta as delta

import monitors
from environments import MLCEnvironment
from experiments import Action, Experiment
from networkgraphs import *
import functools
import random
import logging
import utils

logging.basicConfig(
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


Action.load_config('%s/cmd.conf' % utils.get_module_path())

# 4 Variables: graph type, link change frequency, link quality, network size.
# Results in 16 different experiments.
repetitions = 1
experiment_length = delta(minutes=30).total_seconds()
low_q = functools.partial(random.choice, [5,7,9,11,13])
high_q = functools.partial(random.choice, [3,5])
fast_wait = functools.partial(random.expovariate, 1)
fast_off = functools.partial(random.expovariate, 0.1)
slow_wait = functools.partial(random.expovariate, 1/60)
slow_off = functools.partial(random.expovariate, 1/600)
g1 = NetworkGraph.Lattice([4,4])
g1['name'] = 'Lattice16'
g2 = NetworkGraph.Lattice([10,10])
g2['name'] = 'Lattice100'
g3 = NetworkGraph.Static_Power_Law(16, 32, 3)
g3['name'] = 'PowerLaw16'
g4 = NetworkGraph.Static_Power_Law(100, 200, 3)
g4['name'] = 'PowerLaw100'


structure_graphs = [g1, g2, g3, g4]
graphs = []
for graph in structure_graphs:
    graph_low = graph.copy()
    graph_low.set_quality(low_q)
    graph_high = graph.copy()
    graph_high.set_quality(high_q)
    fast1 = graph_low.copy()
    fast1.random_link_changes(fast_wait, fast_off, experiment_length)
    fast1['name'] = fast1['name'] + '_low_fast'
    fast2 = graph_high.copy()
    fast2.random_link_changes(fast_wait, fast_off, experiment_length)
    fast2['name'] = fast2['name'] + '_high_fast'
    slow1 = graph_low.copy()
    slow1.random_link_changes(slow_wait, slow_off, experiment_length)
    slow1['name'] = slow1['name'] + '_low_slow'
    slow2 = graph_high.copy()
    slow2.random_link_changes(slow_wait, slow_off, experiment_length)
    slow2['name'] = slow2['name'] + '_high_slow'
    graphs.extend([fast1, fast2, slow1,slow2])

#TODO fixes: NetworkMonitor --> how does it know what to filter if its global?
# Link_changes dont change among repetitions!
env = MLCEnvironment()
env.prepare()
for i in range(repetitions):
    for graph in graphs:
        e = Experiment('/tmp/experiment_%s%i_bmx' % (graph['name'],i), topology = graph)
        N = len(graph.vs)
        e.boot(range(N))
        e.start()
        cpu = monitors.CPUMonitor()
        mem = monitors.MemoryMonitor()
        # Monitor connectivity between end points of the longest path:
        (src,dst) = graph.get_longest_path()
        con = monitors.ConnectivityMonitor(src=src, dsts=dst, vlan=2)
        e.monitor(monitors=[con])
        # Start bmx6 in every node:
        inter_delay = functools.partial(random.uniform,0,5)
        routing = e.execute('BMX6', inter_delay=inter_delay, monitors=[cpu,mem])
        # TODO fix so NetworMonitor does filter traffic
        net = monitors.NetworkMonitor(action=routing)
        net.add_interval(delta(minutes=10), delta(minutes=30), tag='steady')
        e.monitor(at=delta(0), monitors=[net])
        e.stop(delay=delta(seconds=experiment_length))
        logging.info(e)
        env.run_experiment(e)
        # Run OLSR:
        e.set_name('/tmp/experiment_%s%i_olsr' % (graph['name'],i))
        e.modify_command(routing, 'OLSR')
        logging.info(e)
        env.run_experiment(e)
        # Run Babel:
        e.set_name('/tmp/experiment_%s%i_babel' % (graph['name'],i))
        e.modify_command(routing, 'Babel')
        logging.info(e)
        env.run_experiment(e)


"""
e = Experiment('/tmp/test',topology=t)
e.boot(range(0,3))
e.start()
#m = monitor('bmx6', NETWORK)
m = monitors.CPUMonitor()
m2 = monitors.MemoryMonitor()
m3 = monitors.NetworkMonitor(outgoing=False)
m3.add_interval(delta(seconds=1), delta(seconds=2), 'test')
m4 = monitors.ConnectivityMonitor(src=0, dsts=[1,2], vlan=2)
e.monitor(monitors=[m3])
e.monitor(monitors=[m4])
bmx_action = e.execute('BMX6', delay=delta(seconds=10), monitors=[m,m2])
e.stop(delay=delta(seconds=5))
print(e)
env = MLCEnvironment()
env.prepare()
env.run_experiment(e)
m3.get_global_data()
m4.get_target_data()
#e.modify_command(bmx_action, 'OLSR')
#env.run_experiment(e)
"""
