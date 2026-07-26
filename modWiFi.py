gsFilNom = "modWiFi.py" #Written in MicroPython for ESP32 WROVER
gsVEERSN = gsFilNom + " V017" #Complete rewrite of flash storage without JSON

#Usage:
#import uasyncio
#from modWiFi import wifiConnect,start_config_portal,wifiIsConnected
#Call wifiConnect and start_config_portal from main():
#EXAMPLE
'''
    # -------------------------------
    # WiFi
    # -------------------------------
    gcRegion,ssid,sIPaddr = wifiConnect(fixed_ip="") # returns 'L' or 'P' or 'E'
    if gcRegion == 'E':
        #Edit flash WiFi Entries on phone browser at sIPaddr
        print("Starting Wi-Fi config edit")
        text2LCD("WiFi: Edit underway") 
        start_config_portal()   
    else:    
        print(f"DB halEsp32 WiFI done {gcRegion}")
        text2LCD("WiFi: Connected")    
'''
#Flash contains a List of Entries. Each Entry has WiFi credentials of a hotspot
#An Entry looks like: SSID::PASSWORD::Region
#The List is worked through until a connection is found
#A Region with more than one character is a disabled Entry so is skipped.
#The first entry will always have an SSID 'EPlace' that can be set as a phone hotspot
#If first Entry connects then the flash list is displayed on the phone browser for editing. 
#When finished [Save] on the phone screen is pressed and the ESP32 is rebooted.

import network
import socket
import time
import machine

ENTRIES_FILE="wifi_entries.dat"
WIFI_CONNECT_TIMEOUT = 20  # seconds per SSID attempt

# -------------------------
# BLOCKING Wi-Fi connect (no asyncio)
# -------------------------
def wifiConnect(fixed_ip=''):
    """
    Blocking Wi-Fi connect for early boot.
    Returns region letter (e.g. 'L' or 'P') or None on failure.
    """
    global gWlan
    gWlan = network.WLAN(network.STA_IF)
    gWlan.active(True)
    lEntries = _load_entries()
    if not lEntries:
        print("No saved Wi-Fi Entries found in ESP32 flash.")
        return None,None,None

    print("Scanning...")
    setVisibleSsids = set() #Make a set of SSIDs available
    try:
        lAps = gWlan.scan()
        print("Found", len(lAps), "APs")
        for tAp in lAps:
            sApSsid = tAp[0].decode()
            setVisibleSsids.add(sApSsid)
            print(repr(tAp[0].decode()), "Channel", tAp[2], "RSSI", tAp[3])
    except Exception as e:
        print("Scan failed:", e)

    for sEntry in lEntries:
        try:
            ssid,pwd,region = _validate_entry(sEntry)
        except ValueError as e:
            print("Ignoring invalid entry:", e)
            continue 
            
        if ssid not in setVisibleSsids:
            print("Skipping", ssid, "- not found in scan")
            continue            
                      
        if region not in ("L", "P"):
            continue #An x appended disables an Entry e.g. Lx
            
        print("Trying Wi-Fi:", ssid)
        try:
            gWlan.disconnect()
        except:
            pass
            
        t0 = time.time()
        while gWlan.isconnected():
            if time.time() - t0 > 2:
                break
            time.sleep(0.1)            
            
        try:
            gWlan.connect(ssid, pwd)
        except Exception as e:
            print("gWlan.connect() error:", e)
            continue
            
        t0 = time.time()
        while not gWlan.isconnected():
            if time.time() - t0 > WIFI_CONNECT_TIMEOUT:
                print("Timeout connecting to", ssid)
                break
            time.sleep(0.2)
            
        if gWlan.isconnected():
            if fixed_ip:
                try:
                    gWlan.ifconfig((fixed_ip, "255.255.255.0", "192.168.0.1", "8.8.8.8"))
                except Exception as exz:
                    print("gWlan.ifconfig() error:", exz)
                    pass
            print("Connected:", gWlan.ifconfig(), " Channel:", gWlan.config('channel'))
            if ssid == "EPlace":
                region = 'E' #Edit
            sIPaddr = gWlan.ifconfig()[0] #Needed for edit on phone browser
            return region, ssid, sIPaddr
    return None,None,None
    
def wifiIsConnected():
    #Returns True id WiFi is connected
    return gWlan.isconnected()      
    
# -------------------------
# Editing portal
# If phone's hotspot is active with SSID = 'EPlace' 
# copy flash Entries to phone's HTML browser for editing
# When finished [Save] is pressed and the ESP32 is rebooted. 
# -------------------------    
def start_config_portal():
    """Allow WiFi Entry list to be edited from a browser."""
    print("DEBUG EPlace connected - Editing mode")
    print("Portal starting")
    print("Connected:", gWlan.isconnected())
    print("ifconfig:", gWlan.ifconfig())
    print("Channel:", gWlan.config("channel"))
    try:
        print("SSID:", gWlan.config("ssid"))
    except:
        pass    
    sServer=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sServer.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sServer.bind(("",80))
    sServer.listen(1)
    sIp = gWlan.ifconfig()[0]
    print("Open browser to http://", sIp)
    print("Connected?", gWlan.isconnected())
    print("Status:", gWlan.status())
    print("ifconfig:", gWlan.ifconfig())
    while True:
        conn,addr=sServer.accept()
        print("Browser connected from",addr)
        try:
            sRequest=conn.recv(4096).decode()
            if sRequest.startswith("GET"):
                sHtml=_build_editor_html()
                conn.send("HTTP/1.1 200 OK\r\n")
                conn.send("Content-Type: text/html\r\n\r\n")
                conn.send(sHtml)
            elif sRequest.startswith("POST"):
                sText=_extract_post_entries(sRequest)
                _save_edited_entries(sText)
                conn.send("HTTP/1.1 200 OK\r\n")
                conn.send("Content-Type: text/html\r\n\r\n")
                conn.send('<html><body style="font-size:18px">')
                conn.send("<h2>Saved.</h2>")
                conn.send("<p>Rebooting...</p>")
                conn.send("</body></html>")
                conn.close()
                time.sleep(3)
                machine.reset()
                return
            else:
                conn.send("HTTP/1.1 400 Bad Request\r\n\r\n")
        except Exception as e:
            conn.send("HTTP/1.1 200 OK\r\n")
            conn.send("Content-Type: text/html\r\n\r\n")
            conn.send('<html><body style="font-size:18px">')
            conn.send("<h2>Error</h2>")
            conn.send("<pre>")
            conn.send(str(e))
            conn.send("</pre>")
            conn.send("<p>Flash not altered.</p>")
            conn.send("</body></html>")
        finally:
            try:
                conn.close()
            except:
                pass
                
def _build_editor_html():
    """Return HTML page containing WiFi Entry editor."""
    sHtml="""<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 WiFi Editor</title>
</head>
<body style="font-size:18px">
<h2>ESP32 WiFi Editor</h2>
<p>Edit one Entry per line.</p>
<p>Format: SSID::Password::Region</p>
<form method="POST">
<textarea name="entries" rows="14" cols="48" spellcheck="false">
"""
    sHtml+="\n".join(_load_entries())
    sHtml+="""
</textarea>
<br><br>
<input type="submit" value="Save">
</form>
</body>
</html>
"""
    return sHtml    
    
def _extract_post_entries(sRequest):
    #Return textarea contents from HTTP POST request.
    iPos=sRequest.find("\r\n\r\n")
    if iPos<0:
        raise ValueError("POST body missing")
    sBody=sRequest[iPos+4:]
    if not sBody.startswith("entries="):
        raise ValueError("Entries field missing")
    sText=sBody[8:]
    return _url_unquote(sText)
    
def _save_edited_entries(sText):
    #Validate edited Entries then save to flash.
    lEntries=[]
    for sLine in sText.split("\n"):
        sLine=sLine.strip()
        if not sLine:
            continue
        _validate_entry(sLine)
        lEntries.append(sLine)
    if not lEntries:
        raise ValueError("No Entries supplied")
    if not _save_entries(lEntries):
        raise OSError("Failed to write flash")

def _url_unquote(sText):
    # Decode application/x-www-form-urlencoded text.
    sText = sText.replace("+", " ")
    i = 0
    sResult = ""
    while i < len(sText):
        if sText[i] == "%" and i + 2 < len(sText):
            try:
                sResult += chr(int(sText[i + 1:i + 3], 16))
                i += 3
                continue
            except:
                pass
        sResult += sText[i]
        i += 1
    return sResult

# -------------------------
# WiFi Entry parser
# -------------------------
def _validate_entry(sEntry):
    #Return (SSID,password,region) from one flash entry.
    if not isinstance(sEntry,str):
        raise ValueError("Entry must be a string")
    lFields=sEntry.strip().split("::")
    if len(lFields)!=3:
        raise ValueError("Each entry must contain exactly two '::'")
    sSSID=lFields[0].strip()
    sPassword=lFields[1].strip()
    sRegion=lFields[2].strip()
    if not sSSID:
        raise ValueError("SSID empty")
    if not sPassword:
        raise ValueError("Password empty")
    if not sRegion:
        raise ValueError("Region empty")
    return sSSID,sPassword,sRegion

# -------------------------
# Flash entry store
# -------------------------
def _load_entries():
    #Read all WiFi entries from flash.
    lEntries=[]
    try:
        with open(ENTRIES_FILE,"r") as f:
            for sLine in f:
                sLine=sLine.strip()
                if sLine:
                    lEntries.append(sLine)
    except OSError:
        pass
    return lEntries

def _save_entries(lEntries):
    #Replace flash contents with supplied WiFi entries.
    try:
        with open(ENTRIES_FILE,"w") as f:
            for sEntry in lEntries:
                f.write(sEntry+"\n")
        return True
    except OSError:
        return False

