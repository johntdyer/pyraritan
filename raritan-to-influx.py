#!/usr/bin/python


from raritan.rpc import Agent, pdumodel, BulkRequestHelper, firmware, sensors
import numpy as np
import sys
import logging
import os

import math
# sys.path.append("pdu-python-api")
from raritan import rpc

sys.path.append(os.path.dirname(os.path.realpath(__file__)))


# from raritan.rpc import Agent, pdumodel, firmware, devsettings, sensors, bulkrpc
# import raritan.rpc.bulkrpc
from collections import deque

import time

from influxdb import InfluxDBClient
import uuid
import random

# Program start time
start_time = time.time()

log = logging.getLogger(__name__)
out_hdlr = logging.StreamHandler(sys.stdout)
out_hdlr.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
out_hdlr.setLevel(logging.INFO)
log.addHandler(out_hdlr)
log.setLevel(logging.INFO)

client = InfluxDBClient(host='localhost', port=8086)

agent = Agent("https", '192.168.160.170', 'admin', 'admin', disable_certificate_verification=True)
pdu = pdumodel.Pdu("/model/pdu/0", agent)
firmware_proxy = firmware.Firmware("/firmware", agent)

outlets = pdu.getOutlets()
firmware_version = firmware_proxy.getVersion()
log.info("starting application")

log.debug("initilize variables")
tot_amps = 0
voltages = []
currents = []
power_factors = []
outlet_states = []
active_powers = []
cycle_count = 1

log.info("starting polling loop")
while True:
    log.info(
        'begin data poll - interval number: %s, program run time: %s seconds' % (cycle_count, math.ceil(time.time() - start_time))
    )

    bulk = rpc.BulkRequestHelper(agent)
    for outlet in outlets:
        bulk.add_request(outlet.getMetaData)
    for outlet in outlets:
        bulk.add_request(outlet.getSensors)
    for outlet in outlets:
        bulk.add_request(outlet.getSettings)

    influx_data_frame = []

    metadatas_and_sensors = bulk.perform_bulk()

    # 3 arrays of bulk request responses
    split = np.array_split(metadatas_and_sensors, 3)

    # metadata results
    outlet_metadatas = split[0]
    # sensor results
    outlet_sensors = split[1]

    # settings results
    outlet_settings = split[2]

    # second bulk request: retrieve sensor readings from all outlets
    bulk = rpc.BulkRequestHelper(agent)
    for os_type in outlet_sensors:
        if os_type.voltage:
            bulk.add_request(os_type.voltage.getReading)
        if os_type.current:
            bulk.add_request(os_type.current.getReading)
        if os_type.powerFactor:
            bulk.add_request(os_type.powerFactor.getReading)
        if os_type.activePower:
            bulk.add_request(os_type.activePower.getReading)
        if os_type.outletState:
            bulk.add_request(os_type.outletState.getState)
    all_readings = deque(bulk.perform_bulk())

    for os_type in outlet_sensors:
        if os_type.voltage:
            voltages.append(all_readings.popleft())
        else:
            voltages.append(None)

        if os_type.current:
            currents.append(all_readings.popleft())
        else:
            currents.append(None)

        if os_type.powerFactor:
            power_factors.append(all_readings.popleft())
        else:
            power_factors.append(None)

        if os_type.activePower:
            active_powers.append(all_readings.popleft())
        else:
            active_powers.append(None)

        if os_type.outletState:
            outlet_states.append(all_readings.popleft())
        else:
            outlet_states.append(None)

    for i in range(len(outlets)):
        # settings = outlet_settings[i]

        s_name = s_num = s_wattage = s_voltage = s_current = s_state = s_pf = ''

        s_name = outlet_settings[i].name if outlet_settings[i].name != "" else "none"

        metadata = outlet_metadatas[i]
        s_num = metadata.label

        wattages = active_powers[i]
        if wattages:
            s_wattage = (wattages.value if wattages.valid else 0.0)

        voltage = voltages[i]
        if voltage:
            s_voltage = (voltage.value if voltage.valid else 0.0)

        current = currents[i]
        if current:
            tot_amps += current.value
            s_current = (current.value if current.valid else 0.0)

        power_factor = power_factors[i]
        if power_factor:
            s_pf = power_factor.value

        outlet_state = outlet_states[i]
        if outlet_state:
            if outlet_state.available:
                if outlet_state.value == sensors.Sensor.OnOffState.ON.val:
                    outlet_onoff = 1
                else:
                    outlet_onoff = 0
            else:
                outlet_onoff = -1
            s_state = outlet_onoff

        data_end_time = int(time.time() * 1000)
        influx_data_frame.append('raritan.pdu.switch,switch_num={switch_no}i,'
            'switch_state={switch_state}i,firmware_ver={firmware},'
            'switch_name={switch_name} current={switch_current},power={switch_wattage},'
            'voltage={switch_voltage} {timestamp}'.format(
                switch_no=s_num,
                switch_state=s_state,
                firmware=firmware_version.replace(' ', '\ '),
                switch_name=s_name.replace(" ", "\ "),
                switch_current=s_current,
                switch_wattage=s_wattage,
                switch_voltage=s_voltage,
                timestamp=data_end_time
            )
        )

    influx_data_frame.append(
        'raritan.pdu.total_current value={total_amps} {timestamp}'.format(total_amps=tot_amps, timestamp=data_end_time))

    client.write_points(influx_data_frame, database='telegraf',
        time_precision='ms', batch_size=10000, protocol='line')
    log.debug("sleeping")
    time.sleep(30)
    cycle_count += 1
