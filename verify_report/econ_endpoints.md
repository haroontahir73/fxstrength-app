# Economic-calendar endpoint probe — 2026-10-18 10:45 UTC

Keys present: FRED=True EIA=False e-Stat=False StatsNZ=False

| Ccy | Source | Provides | OK | HTTP | ms | Latest | Key |
|---|---|---|---|---|---|---|---|
| USD | FRED release calendar (future dates) | scheduled release dates | ❌ | 400 | 491 | — | key required; FRED key present |
| USD | FRED series obs CPIAUCSL | actual + previous values | ❌ | 400 | 283 | — | key required; present |
| USD | FRED/ALFRED vintages PAYEMS | first-release + revisions | ❌ | 400 | 220 | — | key required; present |
| USD | BLS public API v1 CPI | actual (keyless, limited) | ✅ | 200 | 214 | — | keyless (v1 limited; v2 optional free key) |
| EUR | Eurostat HICP YoY (prc_hicp_manr) | actual + previous | ✅ | 200 | 389 | 2025-12 | keyless |
| EUR | ECB main refinancing rate (FM) | central-bank rate | ✅ | 200 | 534 | 2026-09-16 | keyless |
| GBP | ONS timeseries CPI (D7G7/MM23) | actual + previous | ❌ | 404 | 495 | — | keyless |
| GBP | ONS beta releases calendar | scheduled releases | ❌ | 404 | 686 | — | keyless |
| GBP | BoE Bank Rate (IUDBEDR CSV) | central-bank rate | ✅ | 200 | 826 | — | keyless |
| CAD | Bank of Canada Valet (overnight rate) | central-bank rate | ✅ | 200 | 153 | 2026-10-15 | keyless |
| CAD | StatCan WDS CPI vector | actual + previous | ✅ | 200 | 518 | 2026-08-01 | keyless |
| AUD | ABS Data API CPI | actual + previous | ✅ | 200 | 2715 | — | keyless |
| AUD | RBA cash rate CSV (f1.1) | central-bank rate | ✅ | 200 | 663 | — | keyless |
| JPY | e-Stat getStatsList | actual (needs free appId) | ✅ | 200 | 786 | — | free key (appId) required; MISSING - unauth probe |
| NZD | Stats NZ APIM host | actual (needs free key) | ❌ | 502 | 831 | — | free subscription key required; MISSING - reachability only |
| CHF | SNB data cube (policy rate snboffzisa) | central-bank rate | ✅ | 200 | 1077 | 2023-07 | keyless |
| CHF | BFS PX-Web API root | actual (statistics) | ✅ | 200 | 783 | — | keyless |
| WTI | EIA v2 weekly crude stocks | inventories (needs free key) | ❌ | 403 | 449 | — | free key required; MISSING - expect 403/registration notice |
| WTI | EIA petroleum release schedule page | release dates | ✅ | 200 | 78 | — | keyless (HTML schedule) |

## Per-source detail

### USD — FRED release calendar (future dates)
- **url**: https://api.stlouisfed.org/fred/releases/dates?include_release_dates_with_no_data=true&sort_order=asc&limit=25&api_key=REDACTED&file_type=json
- **status**: 400
- **ms**: 491
- **error_body**: {"error_code":400,"error_message":"Bad Request.  The value for variable api_key is not registered.  Read https:\/\/fred.stlouisfed.org\/docs\/api\/api_key.html for more information."}

### USD — FRED series obs CPIAUCSL
- **url**: https://api.stlouisfed.org/fred/series/observations?series_id=CPIAUCSL&sort_order=desc&limit=3&api_key=REDACTED&file_type=json
- **status**: 400
- **ms**: 283
- **error_body**: {"error_code":400,"error_message":"Bad Request.  The value for variable api_key is not registered.  Read https:\/\/fred.stlouisfed.org\/docs\/api\/api_key.html for more information."}

### USD — FRED/ALFRED vintages PAYEMS
- **url**: https://api.stlouisfed.org/fred/series/observations?series_id=PAYEMS&realtime_start=2026-01-01&realtime_end=2026-10-01&limit=6&api_key=REDACTED&file_type=json
- **status**: 400
- **ms**: 220
- **error_body**: {"error_code":400,"error_message":"Bad Request.  The value for variable api_key is not registered.  Read https:\/\/fred.stlouisfed.org\/docs\/api\/api_key.html for more information."}

### USD — BLS public API v1 CPI
- **url**: https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0
- **status**: 200
- **ms**: 214
- **parsed**: json-ok
- **top_keys**: ['status', 'responseTime', 'message', 'Results']
- **sample**: {"status":"REQUEST_SUCCEEDED","responseTime":112,"message":[],"Results":{
"series":
[{"seriesID":"CUUR0000SA0","data":[{"year":"2026","period":"M09","periodName":"September","latest":"true","value":"336.940","footnotes":[{}]},{"year":"2026","period":"M08","periodName":"August","v

### EUR — Eurostat HICP YoY (prc_hicp_manr)
- **url**: https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr?format=JSON&lang=EN&coicop=CP00&geo=EA&lastTimePeriod=3
- **status**: 200
- **ms**: 389
- **parsed**: json-ok
- **top_keys**: ['version', 'class', 'label', 'source', 'updated', 'value', 'id', 'size', 'dimension', 'extension']
- **latest_date**: 2025-12
- **sample**: {"version":"2.0","class":"dataset","label":"HICP - monthly data (annual rate of change)","source":"ESTAT","updated":"2026-02-06T23:00:00+0100","value":{"0":2.1,"1":2.1,"2":2.0},"id":["freq","unit","coicop","geo","time"],"size":[1,1,1,1,3],"dimension":{"freq":{"label":"Time freque

### EUR — ECB main refinancing rate (FM)
- **url**: https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.MRR_FR.LEV?format=jsondata&lastNObservations=1
- **status**: 200
- **ms**: 534
- **parsed**: json-ok
- **top_keys**: ['header', 'dataSets', 'structure']
- **latest_date**: 2026-09-16
- **sample**: {"header":{"id":"edc4d565-7e62-4df6-bc71-e273a74bf60a","test":false,"prepared":"2026-10-08T14:42:17.828+02:00","sender":{"id":"ECB"}},"dataSets":[{"action":"Replace","validFrom":"2026-10-08T14:42:17.827+02:00","series":{"0:0:0:0:0:0:0":{"attributes":[0,null,0,0,null,null,null,nul

### GBP — ONS timeseries CPI (D7G7/MM23)
- **url**: https://api.ons.gov.uk/timeseries/D7G7/dataset/MM23/data
- **status**: 404
- **ms**: 495
- **error_body**: This API has been decommissioned as part of a suite of work to improve the digital products and services we offer. It was fully retired on 25/11/2024. If you have any queries please contact us at apiservice@ons.gov.uk.


### GBP — ONS beta releases calendar
- **url**: https://api.beta.ons.gov.uk/v1/releases?limit=5
- **status**: 404
- **ms**: 686
- **error_body**: 404 page not found


### GBP — BoE Bank Rate (IUDBEDR CSV)
- **url**: https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2025&Dateto=now&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N
- **status**: 200
- **ms**: 826
- **parsed**: text-ok
- **lines**: 454
- **sample**: DATE,IUDBEDR
02 Jan 2025,4.75
03 Jan 2025,4.75
06 Jan 2025,4.75

### CAD — Bank of Canada Valet (overnight rate)
- **url**: https://www.bankofcanada.ca/valet/observations/V39079/json?recent=2
- **status**: 200
- **ms**: 153
- **parsed**: json-ok
- **top_keys**: ['terms', 'seriesDetail', 'observations']
- **latest_date**: 2026-10-15
- **sample**: {
    "terms": {
        "url": "https://www.bankofcanada.ca/terms/"
    },
    "seriesDetail": {
        "V39079": {
            "label": "Target for the overnight rate (business daily)",
            "description": "Also called the policy interest rate, the average rate that the

### CAD — StatCan WDS CPI vector
- **url**: https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods
- **status**: 200
- **ms**: 518
- **parsed**: json-ok
- **top_keys**: list[1]
- **latest_date**: 2026-08-01
- **sample**: [{"status":"SUCCESS","object":{"responseStatusCode":0,"productId":18100004,"coordinate":"2.2.0.0.0.0.0.0.0.0","vectorId":41690973,"vectorDataPoint":[{"refPer":"2026-06-01","refPer2":"","refPerRaw":"2026-06-01","refPerRaw2":"","value":169.0,"decimals":1,"scalarFactorCode":0,"symbo

### AUD — ABS Data API CPI
- **url**: https://data.api.abs.gov.au/rest/data/CPI/all?lastNObservations=1&format=jsondata
- **status**: 200
- **ms**: 2715
- **parsed**: json-ok
- **top_keys**: ['meta', 'data', 'errors']
- **sample**: {"meta":{"schema":"https://raw.githubusercontent.com/sdmx-twg/sdmx-json/master/data-message/tools/schemas/2.0.0/sdmx-json-data-schema.json","id":"IREF014766","prepared":"2026-10-18T10:45:15Z","test":true,"contentLanguages":["en"],"sender":{"id":"Stable_-_DotStat_v8","name":"unkno

### AUD — RBA cash rate CSV (f1.1)
- **url**: https://www.rba.gov.au/statistics/tables/csv/f1-data.csv
- **status**: 200
- **ms**: 663
- **parsed**: text-ok
- **lines**: 4006
- **sample**: ﻿F1 INTEREST RATES AND YIELDS – MONEY MARKET
Title,Cash Rate Target,Change in the Cash Rate Target,Interbank Overnight Cash Rate,Highest Interbank Overnight Cash Rate,Lowest Interbank Overnight Cash Rate,Volume of Cash Market Transactions,Number of Cash Market Transactions,Total Return Index,EOD 1-month BABs/NCDs,EOD 3

### JPY — e-Stat getStatsList
- **url**: https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList?appId=&limit=1
- **status**: 200
- **ms**: 786
- **parsed**: json-ok
- **top_keys**: ['GET_STATS_LIST']
- **sample**: {"GET_STATS_LIST":{"RESULT":{"STATUS":100,"ERROR_MSG":"\u8A8D\u8A3C\u306B\u5931\u6557\u3057\u307E\u3057\u305F\u3002\u30A2\u30D7\u30EA\u30B1\u30FC\u30B7\u30E7\u30F3ID\u3092\u78BA\u8A8D\u3057\u3066\u4E0B\u3055\u3044\u3002","DATE":"2026-10-18T19:45:18.824+09:00"},"PARAMETER":{"DATA_

### NZD — Stats NZ APIM host
- **url**: https://api.stats.govt.nz/
- **status**: 502
- **ms**: 831
- **error_body**: <html>
<head><title>502 Bad Gateway</title></head>
<body>
<center><h1>502 Bad Gateway</h1></center>
<hr><center>Microsoft-Azure-Application-Gateway/v2</center>
</body>
</html>


### CHF — SNB data cube (policy rate snboffzisa)
- **url**: https://data.snb.ch/api/cube/snboffzisa/data/json/en
- **status**: 200
- **ms**: 1077
- **parsed**: json-ok
- **top_keys**: ['timeseries']
- **latest_date**: 2023-07
- **sample**: {"timeseries":[{"header":[{"dim":"Overview","dimItem":"Switzerland - SNB policy rate"}],"metadata":{"key":"EPB@SNB.snboffzisa{LZ}","frequency":"P1M","scale":"","unit":"In percent"},"values":[{"date":"2019-06","value":-0.75},{"date":"2019-07","value":-0.75},{"date":"2019-08","valu

### CHF — BFS PX-Web API root
- **url**: https://www.pxweb.bfs.admin.ch/api/v1/en/
- **status**: 200
- **ms**: 783
- **parsed**: text-ok
- **lines**: 1
- **sample**: [{"dbid":"px-x-0304010000_101","text":"px-x-0304010000_101"},{"dbid":"px-x-0304010000_111","text":"px-x-0304010000_111"},{"dbid":"px-x-0304010000_112","text":"px-x-0304010000_112"},{"dbid":"px-x-0304010000_121","text":"px-x-0304010000_121"},{"dbid":"px-x-0602050000_101","text":"px-x-0602050000_101"},{"dbid":"px-x-06020

### WTI — EIA v2 weekly crude stocks
- **url**: https://api.eia.gov/v2/petroleum/stoc/wstk/data/?api_key=&frequency=weekly&data[0]=value&sort[0][column]=period&sort[0][direction]=desc&length=2
- **status**: 403
- **ms**: 449
- **error_body**: {
  "error": {
    "code": "API_KEY_MISSING",
    "message": "No api_key was supplied.  Please register for one at https://www.eia.gov/opendata/register.php"
  }
}

### WTI — EIA petroleum release schedule page
- **url**: https://www.eia.gov/petroleum/supply/weekly/schedule.php
- **status**: 200
- **ms**: 78
- **parsed**: text-ok
- **lines**: 1009
- **sample**: <!doctype html>
<html>
<head>
	<title>Weekly Petroleum Status Report Schedule - U.S. Energy Information Administration (EIA)</title>
