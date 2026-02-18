# -*- coding: utf-8 -*-
"""
Broker
"""

import json
import time
import helics as h

################# reading in config file ###########################################
print("reading json")
filename = "Broker.json"
with open(filename) as f:
    data = json.loads(f.read())
    num_federate = data["num_federate"]
print("reading json okay!")


helicsversion = h.helicsGetVersion()
print("Helics version = {}".format(helicsversion))
# start a broker inside the file

#num_federate = num_feeder + 2 # num_feeder*2
brokerinitstring = f"-f {num_federate} " # the number (6) is the number of federates connecting
#
# Create broker #
broker = h.helicsCreateBroker("zmq", "", brokerinitstring)
isconnected = h.helicsBrokerIsConnected(broker)
if isconnected == 1:
    print("Broker created and connected")

while h.helicsBrokerIsConnected(broker):
    time.sleep(1)

h.helicsCloseLibrary()

print("Broker disconnected")

