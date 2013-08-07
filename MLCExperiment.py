#!/usr/bin/python3

from datetime import timedelta as delta

import monitors
from environments import MLCEnvironment
from experiments import Action, Experiment


Action.load_config('/home/ester/PhD/mlc/MLCExperiment/cmd.conf')
t = nx.complete_graph(3)
t.graph['link_changes'] = [ (delta(seconds=1),0,1,0), (delta(seconds=2),0,1,3)]
e = Experiment(topology=t)
e.boot(range(0,3))
e.start()
#m = monitor('bmx6', NETWORK)
m = monitors.CPUMonitor()
#m2 = monitors.MemoryMonitor()
#m3 = monkitors.NetworkMonitor('/tmp/proba',outgoing=False)
m4 = monitors.ConnectivityMonitor('/tmp/proba-pings', src=0, dsts=[1,2], vlan=2)
#e.monitor(monitors=[m3])
e.monitor(monitors=[m4])
e.execute('BMX6', delay=delta(seconds=1), monitors=[m])
#e.apply_dynamic_links()
#m2 = monitor(connectivity, from=N+1, to=A)
#m3 = monitor(connectivity, from=N+1, to=B)

#e.boot(N+1, wait = wait1)
#e.execute(bmx6, nodes=N+1, monitor=[m2,m3])

#e.add_link(N+1,A)
e.stop(delay=delta(seconds=5))
print(e)
env = MLCEnvironment()
env.prepare()
env.run_experiment(e)
