"""Module to fetch stats from a Raritan PDU
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.realpath(__file__)))
from raritan.rpc import Agent, pdumodel, BulkRequestHelper


class Pdu(object):
    """Pdu class for fetching PDU stats
    """

    def __init__(self, hostname, username, password, timeout=60,
                 ssl_verify=False, protocol='https'):
        """Initializer function for PDU module
        """
        self._hostname = hostname
        self._username = username
        self._password = password
        self._timeout = timeout
        self._ssl_verify = ssl_verify
        self._protocol = protocol

        dis_cert_verify = True if ssl_verify is False else False

        # Initialize Raritan RPC agent
        self._agent = Agent(proto=self._protocol, host=self._hostname,
                            user=self._username, passwd=self._password,
                            disable_certificate_verification=dis_cert_verify,
                            timeout=self._timeout)
        self._pdu = pdumodel.Pdu("model/pdu/0", self._agent)
        self._inlet = pdumodel.Inlet("/model/inlet/0", self._agent)

    def get_facts(self):
        """Function to fetch general information from PDU
        """
        metadata = self._pdu.getMetaData()
        nameplate = self._pdu.getNameplate()
        return {
            "fqdn": self._hostname,
            "hostname": self._hostname,
            "model": nameplate.model,
            "os_version": metadata.fwRevision,
            "ratings": {
                "current": nameplate.rating.current,
                "voltage": nameplate.rating.voltage,
                "frequency": nameplate.rating.frequency,
                "power": nameplate.rating.power
            },
            "serial_number": nameplate.serialNumber,
            "vendor": nameplate.manufacturer,
        }

    def get_inlets(self):
        """Function to fetch inlets stats from the PDU
        """
        self._bulk = BulkRequestHelper(self._agent)
        keys = ["activeEnergy_Wh", "activePower_W", "apparentPower_VA",
                "current_A", "lineFrequency_Hz", "powerFactor", "voltage_V"]

        inlets = self._pdu.getInlets()
        for inlet in inlets:
            self._bulk.add_request(inlet.getMetaData)
            self._bulk.add_request(inlet.getSensors)

        response = self._bulk.perform_bulk()
        awaiting = []
        readings = []
        self._bulk.clear()

        for i, inlet in enumerate(inlets):
            metadata = response.pop(0)
            sensors = response.pop(0)
            self._bulk.add_request(sensors.activeEnergy.getReading)
            self._bulk.add_request(sensors.activePower.getReading)
            self._bulk.add_request(sensors.apparentPower.getReading)
            self._bulk.add_request(sensors.current.getReading)
            self._bulk.add_request(sensors.lineFrequency.getReading)
            self._bulk.add_request(sensors.powerFactor.getReading)
            self._bulk.add_request(sensors.voltage.getReading)

            awaiting.append(sensors)

            # Raritan sockets are indexed from 1
            readings.append({
                "index": i + 1,
                "label": metadata.label,
            })

        response = self._bulk.perform_bulk()

        for i, sensors in enumerate(awaiting):
            for key in keys:
                readings[i][key] = response.pop(0).value

        return readings

    def get_outlets(self):
        """Function to get outlet stats of PDU
        """
        self._bulk = BulkRequestHelper(self._agent)
        outlets = self._pdu.getOutlets()

        for outlet in outlets:
            self._bulk.add_request(outlet.getMetaData)
            self._bulk.add_request(outlet.getSensors)

        response = self._bulk.perform_bulk()
        awaiting = []
        readings = []
        self._bulk.clear()

        for i in range(len(outlets)):
            metadata = response.pop(0)
            sensors = response.pop(0)

            possible = {
                "activeEnergy_Wh": sensors.activeEnergy,
                "activePower_W": sensors.activePower,
                "apparentEnergy_Wh": sensors.apparentEnergy,
                "apparentPower_VA": sensors.apparentPower,
                "current_A": sensors.current,
                "lineFrequency_Hz": sensors.lineFrequency,
                "peakCurrent_A": sensors.peakCurrent,
                "powerFactor": sensors.powerFactor,
                "voltage_V": sensors.voltage}

            for name, sensor in possible.items():
                if sensor:
                    self._bulk.add_request(possible[name].getReading)
                    awaiting.append([i, name, lambda x: x])

            if sensors.outletState:
                self._bulk.add_request(sensors.outletState.getState)

                states = {
                    sensors.outletState.OnOffState.OFF.val: "off",
                    sensors.outletState.OnOffState.ON.val: "on",
                }

                awaiting.append([i, "outletState", lambda x: states[x]])

            readings.append({
                "index": i + 1,
                "label": metadata.label,
                "switchable": metadata.isSwitchable,
            })

        response = self._bulk.perform_bulk()

        for val in awaiting:
            (outlet, key, fn) = val
            readings[outlet][key] = fn(response.pop(0).value)

        return readings

    def get_overcurrentprotectors(self):
        """Function to get overcurrent protectors for PDU
        """
        self._bulk = BulkRequestHelper(self._agent)
        overcurrprotectors = self._pdu.getOverCurrentProtectors()

        for oc in overcurrprotectors:
            self._bulk.add_request(oc.getMetaData)
            self._bulk.add_request(oc.getSensors)

        response = self._bulk.perform_bulk()
        self._bulk.clear()

        awaiting = []
        readings = []

        for i in range(len(overcurrprotectors)):
            metadata = response.pop(0)
            sensors = response.pop(0)
            overcurrprotectors_name = metadata.label

            if "FUSE" not in overcurrprotectors_name:
                self._bulk.add_request(sensors.current.getReading)
                awaiting.append([i, "current", lambda x: x])

            readings.append({
                "name": overcurrprotectors_name,
                "rating": metadata.rating.current
            })
        response = self._bulk.perform_bulk()

        for val in awaiting:
            (oc, key, fn) = val
            readings[oc][key] = fn(response.pop(0).value)
            readings[oc]["percent-used"] = round(((readings[oc][key] /
                                                   readings[oc]["rating"]) *
                                                  100), 2)

        return readings

    def get_three_phase_stats(self):
        """Function to fetch three phase stats for current and voltage
        """
        try:
            poles = self._inlet.getPoles()
            three_phase_current = [
                pole.current.getReading().value for pole in poles[0:3]
            ]
            three_phase_voltage = [
                pole.voltage.getReading().value for pole in poles[0:3]
            ]
        except Exception:
            three_phase_current = [0, 0, 0]
            three_phase_voltage = [0, 0, 0]

        return {
            "three-phase-current": {
                "L1": three_phase_current[0],
                "L2": three_phase_current[1],
                "L3": three_phase_current[2]
            },
            "three-phase-voltage": {
                "L1-L2": three_phase_voltage[0],
                "L2-L3": three_phase_voltage[1],
                "L3-L1": three_phase_voltage[2]
            }
        }

    def get_environments(self):
        """Private function to fetch environmental variables like inlet
        and outlet stats
        """
        inlets = self.get_inlets()

        outlets = self.get_outlets()

        overcurrentprotectors = self.get_overcurrentprotectors()

        return {
            'inlets': inlets,
            'outlets': outlets,
            "overcurrentprotectors": overcurrentprotectors
        }

    def poll(self):
        """Function to poll a PDU
        """

        facts = self.get_facts()
        env_stats = self.get_environments()
        three_phase_stats = self.get_three_phase_stats()

        return {
            'facts': facts,
            'env_stats': env_stats,
            'three-phase-stats': three_phase_stats
        }
