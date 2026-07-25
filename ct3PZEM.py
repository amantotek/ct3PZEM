gsFilNom = "ct3PZEM.py" #Written in MicroPython for ESP32 WROOM
gsVEERSN = gsFilNom + " V009" #Include debug boolean speed up and modKeepAlive
# Reads mains power (Solar, Heatpump, House) and publishes to a MQTT cloud
#Updating Over The Air (OTA) from Laptop terminal:
# cd ~/fotmus/malwebDesign/projects/ct3PZEM (Navigate to the project folder)
# cp ct3PZEMv??.py ct3PZEM.py (copy version wanted)
# curl http://192.168.0.71/sv (check version on the ESP32)
# curl -X POST --data-binary @ct3PZEM.py http://192.168.0.71/pub (Upload new version)
# curl http://192.168.0.71/sv (check version has changed)
# Test on laptop: $ cd /home/andymc/fotmus/malwebDesign/projects/ct3PZEM/;python3 -m py_compile ct3PZEMv08.py

import time,gc,uasyncio,os
from machine import UART, Pin, reset
from umqtt.simple import MQTTClient

#Ours
import modOTAserver
from modWiFi import wifiConnect,start_config_portal
import modDateTime
import mqcons
import modMQTpub #V022
import modKeepAlive #V001

gbDebug = True 
#gsTopicPub = b"op/kw"       # ESP32k → Mobile (must be bytes for umqtt)
#sClientId = b"esp32_kw"     #Each device must have unique clientId
gsTopicSub = "op/m2e" #Check for manual update being pressed see halEsp32t.py
ECYGETHALDATA = "GETHALINF"
gfBadReadingMarker = 99.0

#ESP32 RX Pins
RXPINSOLAR      = 16
RXPINHEATPUMP   = 18
RXPINDWELLING   = 19
gledBlu = Pin(2, Pin.OUT) #Blue onboard LED

#Globals
glFixdIP = "192.168.0.71"
giLastMinute = -1
giLastDebugSecond = -1
giFastPublishSecs = 0
giMinsRunning = 0
gfSolarKw = 99.9
gfHeatPumpKw = 99.9
gfDwellingKw = 99.9
giNowHour = 0
giNowMinute = 0
gcDSTsuffixUBG = '?' #Normally G=GMT B=BST and U=Undiscovered
giLEDmSec = 500 #For flashing blue LED
    
def fnUpdatePressed():
    global giFastPublishSecs
    print("fn Update Pressed ")
    giFastPublishSecs = 300 #5 minutes fast updates    

mqttPub = modMQTpub.MQTTPublisher(
     sDeviceLabel=gsVEERSN,
     sBrokerHost=mqcons.sBrokerHost,
     iBrokerPort=mqcons.iBrokerPort,
     sMqttUser=mqcons.sMqttUser,
     sMqttPassword=mqcons.sMqttPassword,
     sTopicPub="op/kw",
     sTopicSub=gsTopicSub,
     sExpectedSubMsg=ECYGETHALDATA,
     fnUpdateCallback=fnUpdatePressed,
     sClientId=None
    )

def publishPowerPayload():
    # Send data to MQTT as JSON
    # Example output:
    #   {"solar":1.2,"hp":0.8,"house":0.4,"time":"23:59G"}
    ##global gfSolarKw, gfHeatPumpKw, gfDwellingKw
    global giNowHour, giNowMinute, gcDSTsuffixUBG
    try:
        sHHMM = "{:02}:{:02}{}".format(
            giNowHour,
            giNowMinute,
            gcDSTsuffixUBG
        )
        dPayload = {
            "solar": round(gfSolarKw,1),
            "hp": round(gfHeatPumpKw,1),
            "house": round(gfDwellingKw,1),
            "time": sHHMM
        }
        mqttPub.fnMQTTPublish(dPayload)
        print("DBCT3 TX:", dPayload)
        return True
    except Exception as e:
        print("DBCT3 Publish error:", e)
        return False
        
def badReadingGet():
    #Every time called this gives a new value
    #It shows a fault power reading
    global gfBadReadingMarker
    fValue = gfBadReadingMarker
    gfBadReadingMarker += 0.1
    if gfBadReadingMarker > 99.9:
        gfBadReadingMarker = 99.0
    return round(fValue, 1)

def probe_largest_block():
    iTestSize = gc.mem_free()
    while iTestSize > 1024:
        try:
            bTest = bytearray(iTestSize)
            del bTest
            return iTestSize
        except:
            iTestSize -= 1024
    return 0

def record_error(sErrMsg):
    print("ERROR:", sErrMsg)

class cPzem004T:
    #Usage:
    #RXPINSOLAR      = 16
    #RXPINHEATPUMP   = 18
    #RXPINDWELLING   = 19
    #cPzem004T = cPzem004T()
    #fKwSolar = cPzem004T.readValues(RXPINSOLAR)
    #fKwHeatpump = cPzem004T.readValues(RXPINHEATPUMP)
    #fKwDwelling = cPzem004T.readValues(RXPINDWELLING)
    
    iTxPin = 17
    iBaud = 9600
    byAddress = b'\xF8'
    oUart = None
    print("DB cPzem004T is initialised")

    # -----------------------------
    # CRC16
    # -----------------------------
    @staticmethod  
    def _calculateCrc16(byData):
        iCrc = 0xFFFF
        for iByte in byData:
            iCrc ^= iByte
            for _ in range(8):
                if iCrc & 1:
                    iCrc >>= 1
                    iCrc ^= 0xA001
                else:
                    iCrc >>= 1
        return bytes([
            iCrc & 0xFF,
            (iCrc >> 8) & 0xFF
        ])

    # -----------------------------
    # Read values
    # -----------------------------
    @staticmethod  
    def readValues(iRxPin):
        fPowerBad = badReadingGet()
        try:
            if cPzem004T.oUart is not None:
                cPzem004T.oUart.deinit()
            print("DB Select RX pin")   
            cPzem004T.oUart=UART(
                2,
                baudrate=cPzem004T.iBaud,
                bits=8,
                parity=None,
                stop=1,
                tx=Pin(cPzem004T.iTxPin),
                rx=Pin(iRxPin),
                timeout=100
            )
            byCommand=bytes([
                cPzem004T.byAddress[0],
                0x04,
                0x00,
                0x00,
                0x00,
                0x0A
            ])
            byPacket = byCommand + cPzem004T._calculateCrc16(byCommand)
            print("DB next dot read")   
            cPzem004T.oUart.read()
            print("DB next dot write")   
            cPzem004T.oUart.write(byPacket)
            time.sleep_ms(200)
            print("DB next dot read25")   
            byResponse=cPzem004T.oUart.read(25)
            print("DB we have response ",byResponse)   
            if byResponse is None:
                return fPowerBad
            if len(byResponse)<13:
                return fPowerBad
            fPowerRaw=(
                (byResponse[9]<<8)|
                byResponse[10]|
                (byResponse[11]<<24)|
                (byResponse[12]<<16)
            )
            return fPowerRaw/10000.0   # deciwatts -> kW
        except Exception as e:
            if gbDebug:
                print("PZEM fail",iRxPin,e)
            return fPowerBad
            
async def ledTask():
    #Flash Blue LED
    while True:
        gledBlu.value(not gledBlu.value())
        await uasyncio.sleep_ms(giLEDmSec)
            
# ---------- Main loop ----------
async def main():
    global glFixdIP, gsFilNom, giLastMinute, giNowHour, giNowMinute, gcDSTsuffixUBG
    global gfSolarKw, gfHeatPumpKw, gfDwellingKw
    global giLEDmSec, giLastDebugSecond, giFastPublishSecs

    print("Versn: {} HEAP start: {} Files: {}".format(gsVEERSN, gc.mem_free(), os.listdir()))
    
    time.sleep(3) #Startup rescue delay allowing ctrl+C 
    
    # -------------------------------
    # 1) WiFi FIRST
    # -------------------------------
    cRegion = wifiConnect(fixed_ip=glFixdIP) # returns 'L', 'P', or None
    if cRegion is None:
        print("Starting Wi-Fi config portal")
        start_config_portal() #This will reboot and hopefully use new wifi setup
        return
    else:
        print("DB halEsp32 WiFI done {}".format(cRegion))

    # -------------------------------
    # 2) MQTT Setup
    # -------------------------------
    print("DB Starting MQTT setup")
    mqttPub.fnMQTTConnectAndSubscribe()
    if mqttPub.bConn:
        print("DB MQTT connected")
    else:
        print("DB MQTT NOT connected")
    
    # -------------------------------
    # 3) OTA server setup
    # -------------------------------
    modOTAserver.startHttpServer(fixedIP=glFixdIP,sComands=None,sVeersion=gsVEERSN,sFileToChnge=gsFilNom)
    
    # -------------------------------
    # 4) Keep Alive
    # -------------------------------    
    modKeepAlive.fnStart()
    
    # -------------------------------
    #  5) Date/Time/DST setup
    # -------------------------------
    sTgudq = modDateTime.fnInitializeModule(cRegion)
    print(sTgudq)
    print(modDateTime.sGetLocalTimeString())
    
    # --------------------------------------------------
    #Send power payload data to MQTT cloud periodically
    # --------------------------------------------------
    # Start async background tasks
    uasyncio.create_task(ledTask())    
    uasyncio.create_task(modKeepAlive.taskKeepAlive())    

    while True:
        modKeepAlive.fnAlive("main")
        # 1) Ensure we have a client & Check subscription to MQTT server
        try:
            if not mqttPub.bConn:
                print("MQTT reconnecting")
                mqttPub.fnMQTTConnectAndSubscribe()
            else:
                mqttPub.fnMQTTCheckSubscriptions()
        except Exception as e:
            print("MQTT error:", e)
            mqttPub.bConn = False        

        # 2) Now safe to publish power/kW to MQTT cloud 
        try:
            year, month, day, giNowHour, giNowMinute, second, iDayOfWeek, gcDSTsuffixUBG = modDateTime.tzGetLocalDateTime()
            bDoRead = False
            if giFastPublishSecs > 0:
                #User pressed Update button on phone
                if second % 10 == 0 and second != giLastDebugSecond:
                    giLastDebugSecond = second
                    giFastPublishSecs -= 10
                    bDoRead = True
            else:
                if giNowMinute != giLastMinute:
                    giLastMinute = giNowMinute
                    bDoRead = True
            if bDoRead == True:
                giLastMinute = giNowMinute
                gfSolarKw = cPzem004T.readValues(RXPINSOLAR)
                gfHeatPumpKw = cPzem004T.readValues(RXPINHEATPUMP)
                gfDwellingKw = cPzem004T.readValues(RXPINDWELLING)
                publishPowerPayload() #send data to MQTT
            sz = min(59, max(0, second)) #Glitch trap
            #Below sec/giLEDmSec 0/920 30/500 59/94 fastest flash
            giLEDmSec = int(((60 - sz) * 14) + 80) #820 // 60 => 14.
        except Exception as e:
            record_error(str(e))
        await uasyncio.sleep(1)

print("DB About to start mainloop")
# ---- Fail-safe run ----
try:
    uasyncio.run(main())
except Exception as e:
    record_error("Fatal: " + str(e))
    time.sleep(1)
    reset()
#-----END
