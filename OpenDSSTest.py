#!/usr/bin/env python3
import os
import opendssdirect as dss

master_dss = "/home/teng/benchmark_118_10_8500_Andes_OpenDSS/8500Node/Master.dss"

dss.Basic.ClearAll()

dss.Text.Command(f'Set DataPath="{os.path.dirname(master_dss)}"')

dss.Text.Command(f'Compile "{master_dss}"')
dss.Text.Command("Set ControlMode=Off")
dss.Solution.Solve()


print("Converged:", dss.Solution.Converged())

# Set an active bus (pick one you know exists; SourceBus is common but not guaranteed)
bus_name = dss.Circuit.AllBusNames()[0]
dss.Circuit.SetActiveBus(bus_name)

print("Active bus:", dss.Bus.Name())
pu = dss.Bus.puVmagAngle()   # [V1pu, ang1, V2pu, ang2, ...]
print("pu Vmag/Ang:", pu)
print("pu magnitudes:", pu[0::2])
print("angles (deg):", pu[1::2])