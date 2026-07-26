gsFilNom = "modKeepAlive.py" #Written in MicroPython for ESP32 WROOM
gsVEERSN = gsFilNom + " V002" #Internalise functions with _
#
# Common keep-alive / watchdog support for ESP32 MicroPython systems.
#
# Features:
# - Hardware WDT support
# - Optional timed automatic restart
# - Uptime reporting
# - Central heartbeat feeding
# - Async monitoring task
#
# Notes:
# - Feed the watchdog ONLY if the system is healthy.
# - Main application should update activity timestamps.
# - giRestartHours=0 disables timed restart feature.
#
# Example:
#
# import uasyncio
# import modKeepAlive
#
# modKeepAlive.fnStart()
#
# uasyncio.create_task(modKeepAlive.taskKeepAlive())
#
# while True:
#     modKeepAlive.fnAlive("main")
#     await uasyncio.sleep(5) #or even better every 1 sec
#

import machine
import time
import gc
import uasyncio

# =========================================================
# USER SETTINGS
# =========================================================

giWdtTimeoutMs=60000
giKeepAliveMaxSeconds=120
giRestartHours=24

# =========================================================
# GLOBALS
# =========================================================

gwdt=None

gdLastAlive={}
gtBootSeconds=time.time()

gbWdtStarted=False

# =========================================================
# INTERNAL
# =========================================================

def _fnLog(sText):
    print("[KEEP]",sText)

def _fnUptimeSeconds():
    return int(time.time()-gtBootSeconds)

def _fnUptimeHours():
    return _fnUptimeSeconds()/3600

def _fnFreeMem():
    gc.collect()
    return gc.mem_free()

# =========================================================
# WATCHDOG CONTROL
# =========================================================

def fnStart():
    global gwdt
    global gbWdtStarted
    if gbWdtStarted:
        return
    _fnLog("Starting WDT")
    gwdt=machine.WDT(timeout=giWdtTimeoutMs)
    gbWdtStarted=True

def _fnFeed():
    global gwdt
    if gwdt is not None:
        gwdt.feed()

# =========================================================
# ALIVE TRACKING
# =========================================================

def fnAlive(sName="main"):
    gdLastAlive[sName]=time.time()

def _fnTaskAgeSeconds(sName):
    if sName not in gdLastAlive:
        return 999999
    return int(time.time()-gdLastAlive[sName])

def _fnAllHealthy():
    if len(gdLastAlive)==0:
        _fnLog("No alive tasks registered")
        return False
    for sName in gdLastAlive:
        iAge=_fnTaskAgeSeconds(sName)
        if iAge>giKeepAliveMaxSeconds:
            _fnLog("Task stale: {} age={}".format(sName,iAge))
            return False
    return True

# =========================================================
# RESTART CONTROL
# =========================================================

def _fnRestart(sReason="unknown"):
    _fnLog("RESTART: {}".format(sReason))
    time.sleep(2)
    machine.reset()

def _fnCheckTimedRestart():
    if giRestartHours<=0:
        return
    fHours=_fnUptimeHours()
    if fHours>=giRestartHours:
        _fnRestart("Timed restart")

# =========================================================
# STATUS
# =========================================================

def _fnStatus():
    d={}
    d["uptime_seconds"]=_fnUptimeSeconds()
    d["uptime_hours"]=round(_fnUptimeHours(),2)
    d["free_mem"]=_fnFreeMem()
    d["tasks"]=len(gdLastAlive)
    return d

def _fnPrintStatus():
    d=_fnStatus()
    _fnLog("uptime_hours={}".format(d["uptime_hours"]))
    _fnLog("free_mem={}".format(d["free_mem"]))
    _fnLog("tasks={}".format(d["tasks"]))
    for sName in gdLastAlive:
        _fnLog("{} age={}".format(sName,_fnTaskAgeSeconds(sName)))

# =========================================================
# MAIN ASYNC TASK
# =========================================================

async def taskKeepAlive():
    _fnLog("taskKeepAlive started")
    while True:
        try:
            _fnCheckTimedRestart()
            if _fnAllHealthy():
                _fnFeed()
            else:
                _fnLog("System unhealthy - watchdog intentionally not fed")
            await uasyncio.sleep(5)
        except Exception as e:
            _fnLog("taskKeepAlive exception {}".format(e))
            await uasyncio.sleep(5)
