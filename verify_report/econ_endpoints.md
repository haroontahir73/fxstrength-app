# Economic-calendar endpoint probe — 2026-10-18 11:31 UTC

Keys present: FRED=True EIA=False e-Stat=False StatsNZ=False

| Ccy | Source | Provides | OK | HTTP | ms | Latest | Key |
|---|---|---|---|---|---|---|---|
| GBP | ONS website CPI annual rate (D7G7/MM23) | actual + previous | ✅ | 200 | 201 | — | keyless (website JSON) |
| GBP | ONS website CPIH annual rate (L55O/MM23) | actual + previous | ✅ | 200 | 108 | — | keyless (website JSON) |
| GBP | ONS website unemployment rate (MGSX/LMS) | actual + previous | ❌ | 404 | 228 | — | keyless (website JSON) |
| GBP | ONS website GDP MoM (ECY2/QNA?) | actual + previous | ❌ | 404 | 247 | — | keyless (website JSON) |
| GBP | ONS release calendar JSON | scheduled releases | ❌ | 404 | 203 | — | keyless (website JSON) |
| GBP | ONS new beta API datasets list | dataset discovery | ✅ | 200 | 502 | — | keyless (new API) |
| CHF | BFS CPI table meta (px-x-0501000000_101) | actual + previous | ❌ | 400 | 703 | — | keyless |
| CHF | BFS CPI table meta (px-x-0501000000_102) | actual + previous | ❌ | 400 | 1647 | — | keyless |
| CHF | BFS PX-Web price-stats db node (05) | navigate to CPI | ❌ | 400 | 530 | — | keyless |
| CHF | SNB cube snbgwdjba (rates) | central-bank rate | ❌ | 404 | 673 | — | keyless |
| CHF | SNB cube snboffzia (policy rate) | central-bank rate | ❌ | 404 | 587 | — | keyless |
| CHF | SNB cube zimoma (money-market rates) | central-bank rate | ✅ | 200 | 1144 | 2023-11 | keyless |
| NZD | Stats NZ OData v1 root | actual (needs free key?) | ❌ | 502 | 919 | — | likely needs free subscription key |
| NZD | RBNZ OCR graph CSV (b2 daily) | central-bank rate | ❌ | 403 | 243 | — | keyless if reachable |
| NZD | RBNZ statistics key graph data | central-bank rate | ❌ | 403 | 46 | — | keyless (HTML) |

## Per-source detail

### GBP — ONS website CPI annual rate (D7G7/MM23)
- **url**: https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data
- **status**: 200
- **ms**: 201
- **parsed**: json-ok
- **top_keys**: ['years', 'quarters', 'months', 'sourceDatasets', 'relatedDatasets', 'relatedDocuments', 'relatedData', 'versions', 'type', 'uri', 'description']
- **sample**: {"years":[{"date":"1989","value":"5.2","label":"1989","year":"1989","month":"","quarter":"","sourceDataset":"MM23","updateDate":"2015-10-12T23:00:00.000Z"},{"date":"1990","value":"7.0","label":"1990","year":"1990","month":"","quarter":"","sourceDataset":"MM23","updateDate":"2015-

### GBP — ONS website CPIH annual rate (L55O/MM23)
- **url**: https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/l55o/mm23/data
- **status**: 200
- **ms**: 108
- **parsed**: json-ok
- **top_keys**: ['years', 'quarters', 'months', 'sourceDatasets', 'relatedDatasets', 'section', 'relatedDocuments', 'relatedData', 'versions', 'type', 'uri', 'description']
- **sample**: {"years":[{"date":"1989","value":"5.7","label":"1989","year":"1989","month":"","quarter":"","sourceDataset":"MM23","updateDate":"2018-12-19T00:00:00.000Z"},{"date":"1990","value":"8.0","label":"1990","year":"1990","month":"","quarter":"","sourceDataset":"MM23","updateDate":"2018-

### GBP — ONS website unemployment rate (MGSX/LMS)
- **url**: https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/mgsx/lms/data
- **status**: 404
- **ms**: 228
- **error_body**: <!DOCTYPE html>
<html lang="en">
    <head>
        <title>Page not found - Office for National Statistics</title>
        <meta charset="utf-8" />
        <meta content="width=device-width,initial-scale=1.0,user-scalable=1" name="viewport"

### GBP — ONS website GDP MoM (ECY2/QNA?)
- **url**: https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ecy2/qna/data
- **status**: 404
- **ms**: 247
- **error_body**: <!DOCTYPE html>
<html lang="en">
    <head>
        <title>Page not found - Office for National Statistics</title>
        <meta charset="utf-8" />
        <meta content="width=device-width,initial-scale=1.0,user-scalable=1" name="viewport"

### GBP — ONS release calendar JSON
- **url**: https://www.ons.gov.uk/releasecalendar/data?size=10&query=
- **status**: 404
- **ms**: 203
- **error_body**: 






    
    
    
    



    
    
    
    



    










<!doctype html>
<html lang="en">
    <head>
        <meta charset="utf-8" />
        <meta http-equiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" c

### GBP — ONS new beta API datasets list
- **url**: https://api.beta.ons.gov.uk/v1/datasets?limit=3
- **status**: 200
- **ms**: 502
- **parsed**: json-ok
- **top_keys**: ['items', 'count', 'offset', 'limit', 'total_count']
- **sample**: {"items":[{"contacts":[{"email":"qualityoflife@ons.gov.uk","name":"Will Shufflebottom, Owain Birrell, Pavan Bains and Geeta Kerai","telephone":"+44 300 0671543"}],"description":"Seasonally and non seasonally-adjusted quarterly estimates of life satisfaction, feeling that the thin

### CHF — BFS CPI table meta (px-x-0501000000_101)
- **url**: https://www.pxweb.bfs.admin.ch/api/v1/en/px-x-0501000000_101/px-x-0501000000_101.px
- **status**: 400
- **ms**: 703
- **error_body**: Bad Request

### CHF — BFS CPI table meta (px-x-0501000000_102)
- **url**: https://www.pxweb.bfs.admin.ch/api/v1/en/px-x-0501000000_102/px-x-0501000000_102.px
- **status**: 400
- **ms**: 1647
- **error_body**: Bad Request

### CHF — BFS PX-Web price-stats db node (05)
- **url**: https://www.pxweb.bfs.admin.ch/api/v1/en/px-x-0501000000_101
- **status**: 400
- **ms**: 530
- **error_body**: Bad Request

### CHF — SNB cube snbgwdjba (rates)
- **url**: https://data.snb.ch/api/cube/snbgwdjba/data/json/en
- **status**: 404
- **ms**: 673
- **error_body**: {"code":"404","message":"Table snbgwdjba not found"}

### CHF — SNB cube snboffzia (policy rate)
- **url**: https://data.snb.ch/api/cube/snboffzia/data/json/en
- **status**: 404
- **ms**: 587
- **error_body**: {"code":"404","message":"Table snboffzia not found"}

### CHF — SNB cube zimoma (money-market rates)
- **url**: https://data.snb.ch/api/cube/zimoma/data/json/en
- **status**: 200
- **ms**: 1144
- **parsed**: json-ok
- **top_keys**: ['timeseries']
- **latest_date**: 2023-11
- **sample**: {"timeseries":[{"header":[{"dim":"Overview","dimItem":"Switzerland - CHF - SARON - 1 day"}],"metadata":{"key":"EPB@SNB.zimoma{SARON}","frequency":"P1M","scale":"","unit":"In percent"},"values":[{"date":"1999-06","value":2.5},{"date":"1999-07","value":0.65},{"date":"1999-08","valu

### NZD — Stats NZ OData v1 root
- **url**: https://api.stats.govt.nz/opendata/v1/
- **status**: 502
- **ms**: 919
- **error_body**: <html>
<head><title>502 Bad Gateway</title></head>
<body>
<center><h1>502 Bad Gateway</h1></center>
<hr><center>Microsoft-Azure-Application-Gateway/v2</center>
</body>
</html>


### NZD — RBNZ OCR graph CSV (b2 daily)
- **url**: https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/b2/hb2-daily.csv
- **status**: 403
- **ms**: 243
- **error_body**: <!DOCTYPE html>
<html lang="en">
<head>
<title>Website unavailable - Reserve Bank of New Zealand - Te Pūtea Matua</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-

### NZD — RBNZ statistics key graph data
- **url**: https://www.rbnz.govt.nz/statistics/key-graphs/key-graph-ocr
- **status**: 403
- **ms**: 46
- **error_body**: <!DOCTYPE html>
<html lang="en">
<head>
<title>Website unavailable - Reserve Bank of New Zealand - Te Pūtea Matua</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-
