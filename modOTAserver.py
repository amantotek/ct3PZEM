gsFilNom = "modOTAserver.py" #Written in MicroPython for ESP32 WROOM
gsVEERSN = gsFilNom + " V003" #

#This allows Over The Air (OTA) remote programming

import usocket, uasyncio, uos, network, machine, time, gc

'''
Usage in host program EXAMPLE
---------------------------------
gsVEERSN="halEsp32t v03k"
import uasyncio
import modOTAserver

def relayOn():
  pass #code this
  return "Relay ON executed"
  
def relayOff():
  pass #code this
  return "Relay OFF executed"

commandHandlers={
    "/rn": relayOn,
    "/rf": relayOff
}

modOTAserver.startHttpServer(
    fixedIP="192.168.0.76",
    sComands=commandHandlers,
    sVeersion=gsVEERSN,
    sFileToChnge="halEsp32t.py"
)

Note: if no extra commands need to be added omit commandHandlers and
set sComands=None

If extra commands are added they can be called for example above like:
curl http://192.168.0.76/rn  # example relay on
curl http://192.168.0.76/rf  # example relay off

Example of Using OTA in Xubuntu terminal
----------------------------------------
Project halEsp32t:
curl http://192.168.0.76/sv (check version on the ESP32)
cd ~/fotmus/malwebDesign/projects/halEsp32t (Navigate to your project folder)
curl -X POST --data-binary @halEsp32t.py http://192.168.0.76/pub (Upload new version)
curl http://192.168.0.76/sv (check version has changed)

Another project heater40C:
curl http://192.168.0.74/rv (check version on the ESP32)
cd ~/fotmus/malwebDesign/projects/heater40C (Navigate to your project folder)
curl -X POST --data-binary @heater40C.py http://192.168.0.74/pub (Upload new version)
curl http://192.168.0.74/rv (check version has changed)

Update a module example:
curl -X POST --data-binary @modWiFi.py http://192.168.0.74/pub/modWiFi.py

Update this OTA module itself!
curl -X POST --data-binary @modOTAserver.py http://192.168.0.74/pub/modOTAserver.py 
'''

# -----------------------------
# Config
# -----------------------------
iHttpChunkSize = 1024 #was 512
gsTempFile = None

# -----------------------------
# Helpers
# -----------------------------
def httpSend(cl, sBody, sCode="200 OK", sType="text/plain"):
    try:
        cl.send(
            "HTTP/1.1 " + sCode +
            "\r\nContent-Type: " + sType +
            "\r\nConnection: close\r\n\r\n" +
            sBody
        )
    except:
        pass


def verifyTempFile():
    try:
        fIn = open(gsTempFile, "r")
        sHead = fIn.read(128)
        fIn.close()
    except OSError:
        return False
    return "gsVEERSN" in sHead


def otaWriteFile(bBody):
    try:
        fOut = open(gsTempFile, "wb")
        fOut.write(bBody)
        fOut.close()
        return verifyTempFile()
    except Exception:
        return False


# -----------------------------
# OTA POST body reader
# -----------------------------
def otaReadPostBody(cl, bFirstData):
    if b"\r\n\r\n" not in bFirstData:
        return None
    bHeader, bRemain = bFirstData.split(b"\r\n\r\n", 1)
    iContentLength = 0
    for bLine in bHeader.split(b"\r\n"):
        if bLine.lower().startswith(b"content-length:"):
            iContentLength = int(bLine.split(b":", 1)[1].strip())
            break
    if iContentLength <= 0:
        return None
    bBody = bRemain
    while len(bBody) < iContentLength:
        bChunk = cl.recv(iHttpChunkSize)
        if not bChunk:
            break
        bBody += bChunk
    if len(bBody) != iContentLength:
        return None
    return bBody

# -----------------------------
# OTA POST handler (CALLED)
# -----------------------------
async def handlePostRequest(cl,bFirstData,sFileToChnge):
    global gsTempFile
    gsTempFile = sFileToChnge + ".new"    
    gc.collect()
    if b"\r\n\r\n" not in bFirstData:
        httpSend(cl,"Bad OTA body","400 Bad Request")
        cl.close()
        return
    bHeader,bRemain = bFirstData.split(b"\r\n\r\n",1)
    iContentLength = 0
    for bLine in bHeader.split(b"\r\n"):
        if bLine.lower().startswith(b"content-length:"):
            iContentLength = int(bLine.split(b":",1)[1].strip())
            break
    if iContentLength <= 0:
        httpSend(cl,"No Content-Length","400 Bad Request")
        cl.close()
        return
    try:
        fOut = open(gsTempFile,"wb")
    except:
        httpSend(cl,"File open failed","500 Internal Server Error")
        cl.close()
        return
    bytesWritten = len(bRemain)
    fOut.write(bRemain)
    while bytesWritten < iContentLength:
        bChunk = cl.recv(iHttpChunkSize)
        if not bChunk:           # Wi-Fi stall or client closed
            raise OSError("OTA socket stalled")        
        fOut.write(bChunk)
        bytesWritten += len(bChunk)
    fOut.close()
    if bytesWritten != iContentLength:
        httpSend(cl,"Incomplete OTA upload","500 Internal Server Error")
        cl.close()
        return
    if not verifyTempFile():
        httpSend(cl,"OTA verify failed","500 Internal Server Error")
        cl.close()
        return
    try:
        cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nOTA OK - Rebooting\n")
        time.sleep(0.5)
    except:
        pass
    cl.close()
    try:
        uos.remove(sFileToChnge+"_old")
    except:
        pass
    try:
        uos.rename(sFileToChnge,sFileToChnge+"_old")
    except:
        pass
    uos.rename(gsTempFile,sFileToChnge)
    machine.reset()

# -----------------------------
# HTTP server task
# -----------------------------
async def httpServerTask(fixedIP, sComands, sVersion, sFileToChnge):
    global gsTempFile
    gsTempFile = sFileToChnge + ".new"

    sSock = usocket.socket()
    sSock.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    sSock.bind(("0.0.0.0", 80))
    sSock.listen(3) #prevents dropped connections during OTA or curl bursts.
    sSock.settimeout(0.2)

    while True:
        try:
            cl, addr = sSock.accept()
        except OSError:
            await uasyncio.sleep_ms(20)
            continue
        
        try:
            bReq = cl.recv(512)
            if not bReq:
                cl.close()
                continue

            sLine = bReq.split(b"\r\n", 1)[0].decode()
            aParts = sLine.split()
            if len(aParts) < 2:
                httpSend(cl, "Bad request", "400 Bad Request")
                cl.close()
                continue

            sMethod = aParts[0]
            sPath = aParts[1]

            if sMethod == "GET" and sPath == "/sv":
                ip = network.WLAN(network.STA_IF).ifconfig()[0]
                httpSend(cl, f"Version: {sVersion}\n")

            elif sMethod == "POST" and sPath.startswith("/pub"):
                if sPath == "/pub":
                    targetFile = sFileToChnge
                else:
                    targetFile = sPath.split("/",2)[2]
                await handlePostRequest(cl, bReq, targetFile)    

            elif sPath in sComands:
                try:
                    result = sComands[sPath]()
                    if hasattr(result, "send"):   # coroutine/generator in MicroPython
                        result = await result
                    if result is None:
                        result = "OK" #Old handlers that return nothing get "OK"
                    httpSend(cl, str(result))
                except Exception as e:
                    httpSend(cl, "Command failed: " + str(e),
                             "500 Internal Server Error")

            else:
                httpSend(cl, "Not found", "404 Not Found")

        except Exception as e:
            httpSend(cl, "Server error: " + str(e),
                     "500 Internal Server Error")

        try:
            cl.close()
        except:
            pass

        await uasyncio.sleep_ms(0)


# -----------------------------
# Public entry point
# -----------------------------
def startHttpServer(fixedIP, sComands=None,
                    sVeersion="unknown",
                    sFileToChnge="halEsp32t.py"):

    if sComands is None:
        sComands = {}

    loop = uasyncio.get_event_loop()
    loop.create_task(
        httpServerTask(fixedIP, sComands, sVeersion, sFileToChnge)
    )

# --- end ---
