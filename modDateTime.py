gsFilNom = "modDateTime.py" #Written in MicroPython for ESP32
gsVEERSN = gsFilNom + " V010" #fnSyncRtcWithNtp fixed
#Coded using MicroPython
# V08 tzGetLocalDateTime returns now a UBG suffix
# Local time via NTP + DST rule installed remotely
# DST rule stored in flash, independent of WiFi credentials
# 
# Caller initialises this module with fnInitializeModule(sRegion)
# sRegion is one uper case character L for London or P for Paris
# For Daylight Savings Time (DST) the caller must setup and the rule
# and can later change or inspect it.
# The caller calls modDateTime.handleDstCommand(sCmd) where sCmd is 'dq' or 'di<rule>' as below examples:
# diUTC0;DST+1;M3.lastSun@01:00UTC-M10.lastSun@01:00UTC (as UK Jan 2026)
# diUTC0;DST0 (if DST is discarded then use this rule instead)
# dq returns stored rule only
# boolGetMinuteFlag (when this changes user determines the minute has changed)
# iGetLocalTimeHM gives hh and mm, current hour and minute.
# sGetLocalTimeString returns a text string like current hh:mmX
# Suffix X meanings:
# G = GMT (DST valid, offset 0)
# B = BST (DST valid, offset calculated)
# U = UTC only (NTP only, DST unknown, rule absent)

import machine
import utime
import ntptime
import uasyncio as asyncio

DST_RULE_FILE = "dst.rule"
DEFAULT_DST_RULE = ""
MIN_DST_RULE_LEN = 8

gRtc = machine.RTC()
gInitialized = False
gUtcOffsetSeconds = 0
gLastNtpSyncEpoch = 0
gLastMinute = -1
gMinuteChanged = False
gCachedTimeString = ""
gRegion = "L"
gDstRuleValid = False
gDstActiveLastCheck = None  # Unknown

#--------------------
# Public Functions
#--------------------

def fnInitializeModule(sRegion):
    global gInitialized,gRegion,gLastNtpSyncEpoch
    if gInitialized == True:
        return #Safeguard against being called more than once
    gRegion = sRegion
    _fnSyncRtcWithNtp() #At initialisation
    gLastNtpSyncEpoch = utime.time()
    _recalculateOffset() #At initialisation
    loop = asyncio.get_event_loop()
    loop.create_task(_backgroundTask())
    gInitialized = True
    return gsVEERSN + " is initialised"

def tzGetLocalDateTime():
    """
    Mimics old NTP tuple but with DST applied.
    Returns:
      year, month, day, hour, minute, second, dayOfWeek
    Where:
      month = 1..12
      dayOfWeek = 1=Mon .. 7=Sun
    Example of Use:
    year, month, day, hour, minute, second, dow = modDateTime.tzGetLocalDateTime()  
    """
    # Current UTC epoch from RTC
    utcEpoch = utime.time()
    # Apply DST / region offset already calculated
    localEpoch = utcEpoch + gUtcOffsetSeconds
    # Convert to local time tuple
    # utime.localtime(): (year, month, mday, hour, minute, second, weekday, yearday)
    t = utime.localtime(localEpoch)
    year   = t[0]
    month  = t[1]
    day    = t[2]
    hour   = t[3]
    minute = t[4]
    second = t[5]
    # MicroPython: weekday = 0 (Mon) .. 6 (Sun)
    # Convert to:   1 (Mon) .. 7 (Sun)
    dayOfWeek = t[6] + 1
    sSuffix = _getSuffix()
    return year, month, day, hour, minute, second, dayOfWeek, sSuffix

def sGetLocalTimeString():
    #Deliver format as **Note1 HHMMx **
    global gMinuteChanged
    if gMinuteChanged == True:
      gMinuteChanged = False
      return gCachedTimeString
    return ""

def handleDstCommand(sCmd):
    if sCmd.startswith("di"):
        sRule = sCmd[2:].strip().replace("–","-")
        ok,reason = _validateDstRule(sRule)
        if not ok:
            return reason
        _saveDstRule(sRule)
        _recalculateOffset()
        if not gDstRuleValid:
            return "ERR:PARSE"
        return "OK"
    if sCmd == "dq":
        return _loadDstRule()
    return "ERR:CMD"

#--------------------
# Local Functions
#--------------------

def _loadDstRule():
    try:
        with open(DST_RULE_FILE,"r") as f:
            return f.read().strip()
    except:
        return DEFAULT_DST_RULE

def _saveDstRule(sRule):
    with open(DST_RULE_FILE,"w") as f:
        f.write(sRule)

def _validateDstRule(sRule):
    if not sRule or len(sRule) < MIN_DST_RULE_LEN:
        return False,"ERR:SHORT"
    for c in sRule:
        if not (("0" <= c <= "9") or ("A" <= c <= "Z") or ("a" <= c <= "z") or c in "+-:;.@"):
            return False,"ERR:CHARS"
    if not sRule.startswith("UTC"):
        return False,"ERR:FORMAT"
    if ";DST" not in sRule:
        return False,"ERR:FORMAT"
    if "DST0" not in sRule:
        if "M" not in sRule or "@" not in sRule or "-" not in sRule:
            return False,"ERR:FORMAT"
    return True,"OK"

def _lastSunday(year,month):
    if month == 12:
        t = utime.mktime((year,12,31,0,0,0,0,0))
    else:
        t = utime.mktime((year,month+1,1,0,0,0,0,0)) - 86400
    while utime.localtime(t)[6] != 6:
        t -= 86400
    return utime.localtime(t)

def _isDstActive(utcTuple,sRule):
    try:
        parts = sRule.split(";")
        base = int(parts[0][3:])
        dst = int(parts[1][3:])
        if dst == 0:
            return False
        p = parts[2].split("-")
        sm = int(p[0][1:p[0].index(".")])
        sh = int(p[0].split("@")[1].split(":")[0])
        em = int(p[1][1:p[1].index(".")])
        eh = int(p[1].split("@")[1].split(":")[0])
        y = utcTuple[0]
        s = _lastSunday(y,sm)
        e = _lastSunday(y,em)
        tsStart = utime.mktime((y,sm,s[2],sh,0,0,0,0))
        tsEnd = utime.mktime((y,em,e[2],eh,0,0,0,0))
        now = utime.mktime(utcTuple)
        return tsStart <= now < tsEnd
    except:
        return False

def _recalculateOffset():
    #Outputs offset in seconds: gUtcOffsetSeconds
    global gUtcOffsetSeconds,gDstRuleValid,gDstActiveLastCheck
    sRule = _loadDstRule()
    parts = sRule.split(";")
    try:
        baseH = int(parts[0][3:])
        dstH = int(parts[1][3:])
        gDstRuleValid = True
    except:
        gUtcOffsetSeconds = 0
        gDstRuleValid = False
        gDstActiveLastCheck = None
        return
    utc = utime.gmtime()    
    dstActive = (dstH > 0 and _isDstActive(utc,sRule))
    offsetH = baseH
    if dstActive:
        offsetH += dstH
    if gRegion == "P":
        offsetH += 1
    gUtcOffsetSeconds = offsetH * 3600
    gDstActiveLastCheck = dstActive
    
def _getSuffix():
    if not gDstRuleValid:
        sSuffix = "U"
    else:
        if gDstActiveLastCheck == False:
            sSuffix = "G"
        elif gDstActiveLastCheck == True:
            sSuffix = "B"
        else:    
            sSuffix = "?"
    return sSuffix

def _sFormatLocalTime(iLocalEpoch):
    h = (iLocalEpoch // 3600) % 24
    m = (iLocalEpoch // 60) % 60
    sSuffix = _getSuffix()
    return "{:02d}:{:02d}{}".format(h,m,sSuffix) # **Note1** HH:MMx where x is U, B or G (no DST rule set, BST or GMT)

def _fnSyncRtcWithNtp():
    try:
        ntptime.settime()
        print("NTP sync OK")
    except Exception as e:
        print("NTP sync FAIL",e)

async def _backgroundTask():
    global gLastMinute,gCachedTimeString,gLastNtpSyncEpoch,gMinuteChanged
    while True:
        utcEpoch = utime.time()
        localEpoch = utcEpoch + gUtcOffsetSeconds
        m = (localEpoch // 60) % 60 #Freshest minute
        if m != gLastMinute:
            gLastMinute = m
            gMinuteChanged = True
            gCachedTimeString = _sFormatLocalTime(localEpoch)
            h = (utcEpoch // 3600) % 24
            if h == 4 and m == 4:
                _fnSyncRtcWithNtp() #RTC is corrected to accurate UTC
                _recalculateOffset() #From DST_RULE_FILE and gRegion
        await asyncio.sleep(1)

def _iGetLocalTimeHM():
    if gCachedTimeString:
        h = int(gCachedTimeString[0:2])
        m = int(gCachedTimeString[3:5])
        return h, m
    return 0, 0

#----END
