gsFilNom = "modMQTpub.py"
gsVEERSN = gsFilNom + " V023"

# Test before OTA on laptop terminal: $ python3 -m py_compile modMQTpub.py
# 19Jul2026 https://chatgpt.com/share/6a5d2474-9660-83eb-b0ac-f65fbaf23da4 Debug stoppage

import ujson
import gc
import network
from umqtt.simple import MQTTClient

def fnGenerateClientId(sDeviceLabel):
    oWlan = network.WLAN(network.STA_IF)
    sMacHex = "".join("{:02x}".format(b) for b in oWlan.config('mac'))
    return sDeviceLabel + "-" + sMacHex

class MQTTPublisher:
    def __init__(
        self,
        sDeviceLabel,
        sBrokerHost,
        iBrokerPort,
        sMqttUser,
        sMqttPassword,
        sTopicPub,
        sTopicSub=None,
        sExpectedSubMsg=None,
        fnUpdateCallback=None,
        sClientId=None
        ):

        if sClientId is not None:
            self.sClientId = sClientId
        else:
            self.sClientId = fnGenerateClientId(sDeviceLabel)

        self.sBrokerHost = sBrokerHost
        self.iBrokerPort = iBrokerPort
        self.sMqttUser = sMqttUser
        self.sMqttPassword = sMqttPassword
        self.sTopicPub = sTopicPub
        self.sTopicSub = sTopicSub
        self.sExpectedSubMsg = sExpectedSubMsg
        self.fnUpdateCallback = fnUpdateCallback
        self.oCli = None
        self.bConn = False
        self.iPingCountdown = 0

    # -----------------------------------
    # MQTT incoming message callback
    # -----------------------------------
    def fnMQTTSubscriptionCallback(self, topic, msg):
        try:
            sTopic = topic.decode()
            sMsg = msg.decode()
            print("DB MQTT RX:", sTopic, sMsg)
            if self.sTopicSub is not None and sTopic == self.sTopicSub:
                if self.sExpectedSubMsg is not None:
                    if sMsg != self.sExpectedSubMsg:
                        return
                #We have correct topic and correct command payload
                if self.fnUpdateCallback is not None:
                    self.fnUpdateCallback()
        except Exception as e:
            print("MQTT callback fail:", e)

    # -----------------------------------
    # connect and subscribe
    # -----------------------------------
    def fnMQTTConnectAndSubscribe(self):
        try:
            gc.collect()
            gc.collect()
            self.oCli = MQTTClient(
                client_id=self.sClientId,
                server=self.sBrokerHost,
                port=self.iBrokerPort,
                user=self.sMqttUser,
                password=self.sMqttPassword,
                ssl=True,
                ssl_params={"server_hostname": self.sBrokerHost},
                keepalive=30
            )
            self.oCli.set_callback(self.fnMQTTSubscriptionCallback)
            self.oCli.connect()
            if self.sTopicSub is not None:
                self.oCli.subscribe(self.sTopicSub)
            self.bConn = True
            print("MQTT connected")
        except Exception as e:
            print("MQTT connect fail:", e)
            self.bConn = False

    # -----------------------------------
    # publish payload
    # -----------------------------------
    def fnMQTTPublish(self, dPayload):
        try:
            if not self.bConn:
                return False
            #print("DBPUB A")
            self.oCli.publish(
                self.sTopicPub,
                ujson.dumps(dPayload),
                retain=True
            )
            #print("DBPUB B TX:", dPayload, self.sTopicPub)
            return True
        except Exception as e:
            print("MQTT publish fail:", e)
            self.bConn = False
            return False

    # -----------------------------------
    # process incoming subscribed messages
    # -----------------------------------            
    def fnMQTTCheckSubscriptions(self):
        try:
            if self.bConn:
                #print("DBCHK A")
                self.oCli.check_msg()
                #print("DBCHK B")                
                self.iPingCountdown += 1
                # Send MQTT ping every 20 seconds
                if self.iPingCountdown >= 20:
                    self.iPingCountdown = 0
                    #print("DBPING A")
                    self.oCli.ping()
                    #print("DBPING B")
                    #print("DBMOD MQTT ping")
        except OSError as e:
            # -1 means no packet available
            if e.args[0] == -1:
                return
            print("MQTT check fail:", e)
            try:
                self.oCli.disconnect()
            except:
                pass
            self.bConn = False
        except Exception as e:
            print("MQTT check fail:", e)
            try:
                self.oCli.disconnect()
            except:
                pass
            self.bConn = False
