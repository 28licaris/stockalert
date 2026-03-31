2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home   
~~API P~~roducts   
~~API Products~~ 

User Guides   
T~~rader API \-~~ Individual 

Market Data Production 

**Market Data Production** 

Specifications 

Documentation 

APIs to access Market Data 

Market Data 

**1.0.0** 

**OAS3** 

Trader API \- Market data 

Contact Schwab Trader API team 

Servers 

https://api.schwabapi.com/marketdata/v1 

Authorize 

Quotes 

Get Quotes Web Service. 

GET/quotes   
Get Quotes by list of symbols. 

Parameters 

Try it out 

Name Description 

Comma separated list of symbol(s) to look up a quote 

symbols string 

(query) 

fields   
string   
(query)   
Example : MRAD,EATOF,EBIZ,AAPL,BAC,AAAHX,AAAIX,$DJI,$SPX,MVEN,SOBS,TOITF,CNSWF,AMZN 230317C01360000,DJX 231215C00290000,/ESH23,./ADUF23C0.55,AUD/CAD 

MRAD,EATOF,EBIZ,AAP 

Request for subset of data by passing coma separated list of root nodes, possible root nodes are quote, fundamental, extended, reference, regular. Sending quote, fundamental in request will return quote and fundamental data in response. Dont send this attribute for full response. 

Default value : all 

file:///Users/licaris/Downloads/market\_data\_api.html 1/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Name Description   
Developer Portal   
**Charles Schwab**   
quote,reference 

**Logo Developer Portal**   
Include indicative symbol quotes for all ETF symbols in request. If ETF symbol ABC is in request and indicative=true API will return quotes for ABC and its corresponding indicative quote for $ABC.IV 

Home   
indicative   
boolean   
Available values : true, false   
API Products   
(query)   
Example : false   
User Guides 

\-- 

Responses 

Code Description Links 

200 Quote Response 

Media type   
application/json   
Controls Accept header.   
Examples   
Search by Symbols+Cusips+SSIDs 

Example Value 

Schema   
No   
links 

{   
"AAPL": {   
"assetMainType": "EQUITY",   
"symbol": "AAPL",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 1973757747,   
"reference": {   
"cusip": "037833100",   
"description": "Apple Inc",   
"exchange": "Q",   
"exchangeName": "NASDAQ"   
},   
"quote": {   
"52WeekHigh": 169,   
"52WeekLow": 1.1,   
"askMICId": "MEMX",   
"askPrice": 168.41,   
"askSize": 400,   
"askTime": 1644854683672,   
"bidMICId": "IEGX",   
"bidPrice": 168.4,   
"bidSize": 400,   
"bidTime": 1644854683633,   
"closePrice": 177.57,   
"highPrice": 169,   
"lastMICId": "XADF",   
"lastPrice": 168.405,   
"lastSize": 200,   
"lowPrice": 167.09,   
"mark": 168.405,   
"markChange": \-9.164999999999992,   
"markPercentChange": \-5.161344821760428,   
"netChange": \-9.165,   
"netPercentChange": \-5.161344821760428,   
"openPrice": 167.37,   
"quoteTime": 1644854683672,   
"securityStatus": "Normal",   
"totalVolume": 22361159,   
"tradeTime": 1644854683408,   
"volatility": 0.0347   
},   
"regular": {   
"regularMarketLastPrice": 168.405,   
"regularMarketLastSize": 2,   
"regularMarketNetChange": \-9.165,   
"regularMarketPercentChange": \-5.161344821760428, "regularMarketTradeTime": 1644854683408   
},   
"fundamental": {   
"avg10DaysVolume": 1,   
"avg1YearVolume": 0,   
"divAmount": 1.1,   
"divFreq": 0,   
"divPayAmount": 0,   
"divYield": 1.1,   
"eps": 0,   
"fundLeverageFactor": 1.1,   
"peRatio": 1.1 

file:///Users/licaris/Downloads/market\_data\_api.html 2/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
}   
},   
**LogoDeveloperPortal**   
"AAAIX": {   
"assetMainType": "MUTUAL\_FUND", 

Home   
"symbol": "AAAIX", "realtime": true, "ssid": \-1,   
API Products   
~~"r~~eference": {   
User Guides   
"cusip": "025085853",   
"description": "AmericanCenturyStrategicAllocation: AggressiveFund-IClass", "exchange": "3",   
"exchangeName": "MutualFund"   
},   
"quote": {   
"52WeekHigh": 9.24,   
"52WeekLow": 7.48,   
"closePrice": 9.12,   
"nAV": 0,   
"netChange": \-0.03,   
"netPercentChange": \-0.32894736842104566, "securityStatus": "Normal",   
"totalVolume": 0,   
"tradeTime": 0   
},   
"fundamental": {   
"avg10DaysVolume": 0,   
"avg1YearVolume": 0,   
"divAmount": 0,   
"divFreq": 0,   
"divPayAmount": 0,   
"divYield": 0.83059,   
"eps": 0,   
"fundLeverageFactor": 0, 

}   
},   
"peRatio": 0 

"AAAHX": {   
"assetMainType": "MUTUAL\_FUND",   
"symbol": "AAAHX",   
"realtime": true,   
"ssid": \-1,   
"reference": {   
"cusip": "02507J789",   
"description": "OneChoiceBlend+2015PortfolioIClass", "exchange": "3",   
"exchangeName": "MutualFund"   
},   
"quote": {   
"52WeekHigh": 10.64,   
"52WeekLow": 9.95,   
"closePrice": 10.53,   
"nAV": 0,   
"netChange": 0,   
"netPercentChange": 0,   
"securityStatus": "Normal",   
"totalVolume": 0,   
"tradeTime": 0   
},   
"fundamental": {   
"avg10DaysVolume": 0,   
"avg1YearVolume": 0,   
"divAmount": 0,   
"divFreq": 0,   
"divPayAmount": 0,   
"divYield": 0,   
"eps": 0,   
"fundLeverageFactor": 0, 

}   
},   
"peRatio": 0 

"BAC": {   
"assetMainType": "EQUITY",   
"symbol": "BAC",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 851234497,   
"reference": {   
"cusip": "060505104",   
"description": "BankOfAmericaCorp",   
"exchange": "N",   
"exchangeName": "NYSE"   
},   
"quote": {   
"52WeekHigh": 48.185,   
"52WeekLow": 22.95,   
"askMICId": "XNYS",   
"askPrice": 47.2,   
"askSize": 2100,   
"askTime": 1644854683639,   
"bidMICId": "XNYS", 

file:///Users/licaris/Downloads/market\_data\_api.html3/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"bidPrice": 47.19, "bidSize": 3700,   
**LogoDeveloperPortal**   
"bidTime": 1644854683640,   
"closePrice": 44.49, 

Home   
"highPrice": 48.185, "lastMICId": "ARCX", "lastPrice": 47.195,   
API Products   
"lastSize": 200,   
User Guides   
"lowPrice": 47.06,   
"mark": 47.195,   
"markChange": 2.7049999999999983, "markPercentChange": 6.080017981568888, "netChange": 2.705,   
"netPercentChange": 6.080017981568888, "openPrice": 48.02,   
"quoteTime": 1644854683640,   
"securityStatus": "Normal",   
"totalVolume": 13573182,   
"tradeTime": 1644854683638,   
"volatility": 0.0206   
},   
"regular": {   
"regularMarketLastPrice": 47.195,   
"regularMarketLastSize": 2,   
"regularMarketNetChange": 2.705,   
"regularMarketPercentChange": 6.080017981568888, "regularMarketTradeTime": 1644854683638 },   
"fundamental": {   
"avg10DaysVolume": 43411957,   
"avg1YearVolume": 40653250,   
"declarationDate": "2021-07-21T05:00:00Z", "divAmount": 0.75,   
"divExDate": "2021-09-02T05:00:00Z",   
"divFreq": 4,   
"divPayAmount": 0.75,   
"divPayDate": "2021-09-24T05:00:00Z",   
"divYield": 1.77,   
"eps": 2.996,   
"fundLeverageFactor": 0,   
"nextDivExDate": "2021-12-27T06:00:00Z", "nextDivPayDate": "2021-12-27T06:00:00Z", 

}   
},   
"peRatio": 13.50133 

"$SPX": {   
"assetMainType": "INDEX",   
"symbol": "$SPX",   
"realtime": true,   
"ssid": 1819771877,   
"reference": {   
"description": "S\&PDOWJONESINDEXS\&P500", "exchange": "0",   
"exchangeName": "Index"   
},   
"quote": {   
"52WeekHigh": 4423.46,   
"52WeekLow": 4385.52,   
"closePrice": 4766.18,   
"highPrice": 4423.46,   
"lastPrice": 4396.2,   
"lowPrice": 4385.52,   
"netChange": \-369.98,   
"netPercentChange": \-7.762610728088331,   
"openPrice": 4412.61,   
"securityStatus": "Unknown",   
"totalVolume": 628009977, 

}   
},   
"tradeTime": 1644854683056 

"MRAD": {   
"assetMainType": "EQUITY",   
"assetSubType": "ETF",   
"symbol": "MRAD",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 67229687,   
"reference": {   
"cusip": "402031868",   
"description": "GuinnessAtkinsonFdsSMARTETFSADVERTISINGMKTTCHETF",   
"exchange": "P",   
"exchangeName": "NYSEArca"   
},   
"quote": {   
"52WeekHigh": 31.96,   
"52WeekLow": 22.18,   
"askMICId": "IEGX",   
"askPrice": 22.29,   
"askSize": 500,   
"askTime": 1644854676848, 

file:///Users/licaris/Downloads/market\_data\_api.html4/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"bidMICId": "EDGX", "bidPrice": 22.22,   
**LogoDeveloperPortal**   
"bidSize": 500,   
"bidTime": 1644854681062, 

Home   
"closePrice": 26.8633, "highPrice": 22.18, "lastPrice": 22.18,   
API Products   
"lastSize": 100,   
User Guides   
"lowPrice": 22.18,   
"mark": 22.22,   
"markChange": \-4.6433,   
"markPercentChange": \-17.284920318799255, "netChange": \-4.6833,   
"netPercentChange": \-17.433822352428777, "openPrice": 22.18,   
"quoteTime": 1644854681062,   
"securityStatus": "Normal",   
"totalVolume": 100,   
"tradeTime": 1644851921969,   
"volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 22.18,   
"regularMarketLastSize": 1,   
"regularMarketNetChange": \-4.6833,   
"regularMarketPercentChange": \-17.433822352428777, "regularMarketTradeTime": 1644851921969 },   
"fundamental": {   
"avg10DaysVolume": 1606,   
"avg1YearVolume": 0,   
"divAmount": 0,   
"divFreq": 0,   
"divPayAmount": 0,   
"divYield": 0,   
"eps": 0,   
"fundLeverageFactor": 0,   
"fundStrategy": "A", 

}   
},   
"peRatio": 0 

"EBIZ": {   
"assetMainType": "EQUITY",   
"assetSubType": "ETF",   
"symbol": "EBIZ",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 52313178,   
"reference": {   
"cusip": "37954Y467",   
"description": "GLOBALXE-COMMERCEETF",   
"exchange": "Q",   
"exchangeName": "NASDAQ"   
},   
"quote": {   
"52WeekHigh": 37.9754,   
"52WeekLow": 24.52,   
"askMICId": "XNAS",   
"askPrice": 24.85,   
"askSize": 200,   
"askTime": 1644854683318,   
"bidMICId": "XNAS",   
"bidPrice": 24.79,   
"bidSize": 200,   
"bidTime": 1644854683318,   
"closePrice": 27.45,   
"highPrice": 24.8303,   
"lastMICId": "XADF",   
"lastPrice": 24.8303,   
"lastSize": 100,   
"lowPrice": 24.52,   
"mark": 24.8303,   
"markChange": \-2.619699999999998,   
"markPercentChange": \-9.543533697632052,   
"netChange": \-2.6197,   
"netPercentChange": \-9.543533697632052,   
"openPrice": 24.55,   
"quoteTime": 1644854683318,   
"securityStatus": "Normal",   
"totalVolume": 1626,   
"tradeTime": 1644850278470,   
"volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 24.8303,   
"regularMarketLastSize": 1,   
"regularMarketNetChange": \-2.6197,   
"regularMarketPercentChange": \-9.543533697632052,   
"regularMarketTradeTime": 1644850278470   
}, 

file:///Users/licaris/Downloads/market\_data\_api.html5/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"fundamental": {   
"avg10DaysVolume": 0,   
**LogoDeveloperPortal**   
"avg1YearVolume": 0,   
"declarationDate": "2020-12-29T06:00:00Z", 

Home   
"divAmount": 0,   
"divExDate": "2020-12-30T06:00:00Z", "divFreq": 1,   
API Products   
"divPayAmount": 0.26641,   
User Guides 

}   
},   
"divPayDate": "2021-01-08T06:00:00Z", "divYield": 0.88276,   
"eps": 0,   
"fundLeverageFactor": 0,   
"fundStrategy": "P",   
"nextDivExDate": "2022-01-10T06:00:00Z", "nextDivPayDate": "2022-01-10T06:00:00Z", "peRatio": 0 

"$DJI": {   
"assetMainType": "INDEX",   
"symbol": "$DJI",   
"realtime": true,   
"ssid": 0,   
"reference": {   
"description": "DowJonesIndustrialAverage", "exchange": "0",   
"exchangeName": "Index"   
},   
"quote": {   
"52WeekHigh": 34744.56,   
"52WeekLow": 34364.39,   
"closePrice": 34738.06,   
"highPrice": 34744.56,   
"lastPrice": 34436.13,   
"lowPrice": 34364.39,   
"netChange": \-301.93,   
"netPercentChange": \-0.8691619508976618, "openPrice": 34694.5,   
"securityStatus": "Unknown",   
"totalVolume": 106647543, 

}   
},   
"tradeTime": 1644854683055 

"AMZN220617C03170000": {   
"assetMainType": "OPTION",   
"symbol": "AMZN220617C03170000",   
"realtime": true,   
"ssid": 72507798,   
"reference": {   
"contractType": "C",   
"daysToExpiration": 123,   
"description": "Amazon.comInc06/17/2022$3170Call",   
"exchange": "o",   
"exchangeName": "OPR",   
"expirationDay": 17,   
"expirationMonth": 6,   
"expirationYear": 2022,   
"isPennyPilot": true,   
"lastTradingDay": 1655510400000,   
"multiplier": 100,   
"settlementType": "P",   
"strikePrice": 3170,   
"underlying": "AMZN",   
"uvExpirationType": "S"   
},   
"quote": {   
"askPrice": 223,   
"askSize": 2,   
"askTime": 0,   
"bidPrice": 217.65,   
"bidSize": 2,   
"bidTime": 0,   
"closePrice": 357.75,   
"delta": 0.5106,   
"gamma": 0.0007,   
"highPrice": 0,   
"impliedYield": 0.042,   
"indAskPrice": 0,   
"indBidPrice": 0,   
"indQuoteTime": 0,   
"lastPrice": 0,   
"lastSize": 0,   
"lowPrice": 0,   
"mark": 220.325,   
"markChange": \-137.425,   
"markPercentChange": \-38.41369671558351,   
"moneyIntrinsicValue": \-40.795,   
"netChange": 0,   
"netPercentChange": 0,   
"openInterest": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html6/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"openPrice": 0,   
"quoteTime": 1644854683379,   
**LogoDeveloperPortal**   
"rho": 4.5173,   
"securityStatus": "Normal", 

Home   
"theoreticalOptionValue": 221.4, "theta": \-0.9619,   
"timeValue": 220.325,   
API Products   
"totalVolume": 0,   
User Guides 

}   
},   
"tradeTime": 0,   
"underlyingPrice": 3129.205, "vega": 7.1633,   
"volatility": 32.8918 

"DJX231215C00290000": {   
"assetMainType": "OPTION",   
"symbol": "DJX231215C00290000",   
"realtime": true,   
"ssid": 69272575,   
"reference": {   
"contractType": "C",   
"daysToExpiration": 669,   
"description": "DOWJONESINDUSIND12/15/2023$290Call", "exchange": "o",   
"exchangeName": "OPR",   
"expirationDay": 15,   
"expirationMonth": 12,   
"expirationYear": 2023,   
"isPennyPilot": true,   
"lastTradingDay": 1702602000000,   
"multiplier": 100,   
"settlementType": "A",   
"strikePrice": 290,   
"underlying": "$DJX",   
"uvExpirationType": "S"   
},   
"quote": {   
"askPrice": 76.95,   
"askSize": 11,   
"askTime": 0,   
"bidPrice": 70.9,   
"bidSize": 11,   
"bidTime": 0,   
"closePrice": 86.2,   
"delta": 0,   
"gamma": 0,   
"highPrice": 0,   
"impliedYield": 0,   
"indAskPrice": 79.55,   
"indBidPrice": 73.25,   
"indQuoteTime": 1644614546536,   
"lastPrice": 0,   
"lastSize": 0,   
"lowPrice": 0,   
"mark": 73.925,   
"markChange": \-12.274999999999991,   
"markPercentChange": \-14.24013921113688,   
"moneyIntrinsicValue": 0,   
"netChange": 0,   
"netPercentChange": 0,   
"openInterest": 0,   
"openPrice": 0,   
"quoteTime": 1644854648305,   
"rho": 0,   
"securityStatus": "Normal",   
"theoreticalOptionValue": 0,   
"theta": 0,   
"timeValue": 0,   
"totalVolume": 0,   
"tradeTime": 0,   
"underlyingPrice": 0,   
"vega": \-999, 

}   
},   
"volatility": 0 

"TOITF": {   
"assetMainType": "EQUITY",   
"symbol": "TOITF",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 68444487,   
"reference": {   
"cusip": "89072T102",   
"description": "TOPICUSCOMINC",   
"exchange": "9",   
"exchangeName": "OTCMarkets",   
"otcMarketTier": "PC"   
},   
"quote": {   
"52WeekHigh": 75.702, 

file:///Users/licaris/Downloads/market\_data\_api.html7/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"52WeekLow": 45.3933, "askPrice": 75.978,   
**LogoDeveloperPortal**   
"askSize": 10000,   
"askTime": 1644849000209, 

Home   
"bidPrice": 72.5951, "bidSize": 10000,   
"bidTime": 1644849000209,   
API Products   
"closePrice": 92.7,   
User Guides   
"highPrice": 75.702,   
"lastPrice": 75.702,   
"lastSize": 100,   
"lowPrice": 72.5478,   
"mark": 75.702,   
"netChange": \-16.998, "openPrice": 74.8977, "quoteTime": 1644854676927, "securityStatus": "Normal", "totalVolume": 4274,   
"tradeTime": 1644854585000, "volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 75.702, "regularMarketLastSize": 1, "regularMarketNetChange": \-16.998, 

}   
},   
"regularMarketTradeTime": 1644854585000 

"EATOF": {   
"assetMainType": "EQUITY",   
"assetSubType": "ETF",   
"symbol": "EATOF",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 43253301,   
"reference": {   
"cusip": "30052J102",   
"description": "EVOLVEAUTMBLINVTNINDXETF", "exchange": "9",   
"exchangeName": "OTCMarkets",   
"otcMarketTier": "EM"   
},   
"quote": {   
"52WeekHigh": 47.1993,   
"52WeekLow": 24.2835,   
"askPrice": 33.1512,   
"askSize": 400000,   
"askTime": 1644849000044,   
"bidPrice": 33.0487,   
"bidSize": 250000,   
"bidTime": 1644849000044,   
"closePrice": 40.198,   
"highPrice": 33.1196,   
"lastPrice": 33.1196,   
"lastSize": 200,   
"lowPrice": 32.82,   
"mark": 33.1196,   
"netChange": \-7.0784,   
"openPrice": 32.82,   
"quoteTime": 1644854660496,   
"securityStatus": "Normal",   
"totalVolume": 1017,   
"tradeTime": 1644850274000,   
"volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 33.1196,   
"regularMarketLastSize": 2,   
"regularMarketNetChange": \-7.0784, 

}   
},   
"regularMarketTradeTime": 1644850274000 

"CNSWF": {   
"assetMainType": "EQUITY",   
"symbol": "CNSWF",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 807850646,   
"reference": {   
"cusip": "21037X100",   
"description": "ConstellationSoftwr",   
"exchange": "9",   
"exchangeName": "OTCMarkets",   
"otcMarketTier": "PC"   
},   
"quote": {   
"52WeekHigh": 1709.738,   
"52WeekLow": 904.0901,   
"askPrice": 1693.4699,   
"askSize": 30000, 

file:///Users/licaris/Downloads/market\_data\_api.html8/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"askTime": 1644849000567, "bidPrice": 1688.4547,   
**LogoDeveloperPortal**   
"bidSize": 20000,   
"bidTime": 1644849000567, 

Home   
"closePrice": 1856.4626, "highPrice": 1709.738, "lastPrice": 1693.4541,   
API Products   
"lastSize": 100,   
User Guides   
"lowPrice": 1680.1511, "mark": 1693.4541,   
"netChange": \-163.0084, "openPrice": 1682.0121, "quoteTime": 1644854655233, "securityStatus": "Normal", "totalVolume": 13901, "tradeTime": 1644854560000, "volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 1693.4541, "regularMarketLastSize": 1,   
"regularMarketNetChange": \-163.0084, 

}   
},   
"regularMarketTradeTime": 1644854560000 

"MVEN": {   
"assetMainType": "EQUITY",   
"symbol": "MVEN",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 39225080,   
"reference": {   
"cusip": "88339B102",   
"description": "ThemavenInc",   
"exchange": "u",   
"exchangeName": "NasdaqOTCBB",   
"otcMarketTier": "QX"   
},   
"quote": {   
"52WeekHigh": 3,   
"52WeekLow": 0.42,   
"askPrice": 0,   
"askSize": 0,   
"askTime": 0,   
"bidPrice": 0,   
"bidSize": 0,   
"bidTime": 0,   
"closePrice": 13.42,   
"highPrice": 0,   
"lastPrice": 0.42,   
"lastSize": 0,   
"lowPrice": 0,   
"mark": 0.42,   
"markChange": \-13,   
"markPercentChange": \-96.87034277198212, "netChange": \-13,   
"netPercentChange": \-96.87034277198212,   
"openPrice": 0,   
"quoteTime": 0,   
"securityStatus": "Normal",   
"totalVolume": 0,   
"tradeTime": 1644353952708,   
"volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 0.42,   
"regularMarketLastSize": 0,   
"regularMarketNetChange": \-13,   
"regularMarketPercentChange": \-96.87034277198212, "regularMarketTradeTime": 1644353952708   
},   
"fundamental": {   
"avg10DaysVolume": 299530,   
"avg1YearVolume": 430760,   
"divAmount": 0,   
"divFreq": 0,   
"divPayAmount": 0,   
"divYield": 0,   
"eps": 0,   
"fundLeverageFactor": 0, 

}   
},   
"peRatio": \-0.68777 

"SOBS": {   
"assetMainType": "EQUITY",   
"symbol": "SOBS",   
"quoteType": "NBBO",   
"realtime": true,   
"ssid": 561081427,   
"reference": { 

file:///Users/licaris/Downloads/market\_data\_api.html9/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"cusip": "83441Q105",   
"description": "SolvayBankCorpSol",   
**LogoDeveloperPortal**   
"exchange": "9",   
"exchangeName": "OTCMarkets", 

Home   
"otcMarketTier": "PC" },   
"quote": {   
API Products   
"52WeekHigh": 43,   
User Guides   
"52WeekLow": 30.28,   
"askPrice": 45,   
"askSize": 200,   
"askTime": 0,   
"bidPrice": 39,   
"bidSize": 100,   
"bidTime": 0,   
"closePrice": 38.219, "highPrice": 0,   
"lastPrice": 38.219,   
"lastSize": 0,   
"lowPrice": 0,   
"mark": 38.219,   
"markChange": 0,   
"markPercentChange": 0, "netChange": 0,   
"netPercentChange": 0, "openPrice": 0,   
"quoteTime": 1644613200189, "securityStatus": "Normal", "totalVolume": 0,   
"tradeTime": 0,   
"volatility": 0   
},   
"regular": {   
"regularMarketLastPrice": 38.219, "regularMarketLastSize": 0,   
"regularMarketNetChange": 0,   
"regularMarketPercentChange": 0,   
"regularMarketTradeTime": 0   
},   
"fundamental": {   
"avg10DaysVolume": 1296,   
"avg1YearVolume": 0,   
"declarationDate": "2021-09-21T05:00:00Z", "divAmount": 1.48,   
"divExDate": "2021-09-30T05:00:00Z", "divFreq": 4,   
"divPayAmount": 1.47,   
"divPayDate": "2021-10-29T05:00:00Z", "divYield": 3.869,   
"eps": 0,   
"fundLeverageFactor": 0,   
"nextDivExDate": "2022-01-31T06:00:00Z", "nextDivPayDate": "2022-01-31T06:00:00Z", 

}   
},   
"peRatio": 0 

"/ESZ21": {   
"assetMainType": "FUTURE",   
"symbol": "/ESZ21",   
"realtime": true,   
"ssid": 0,   
"reference": {   
"description": "E-miniS\&P500IndexFutures,Dec-2021,ETH",   
"exchange": "@",   
"exchangeName": "XCME",   
"futureActiveSymbol": "/ESZ21",   
"futureExpirationDate": 1639717200000,   
"futureIsActive": true,   
"futureIsTradable": true,   
"futureMultiplier": 50,   
"futurePriceFormat": "D,D",   
"futureSettlementPrice": 4696,   
"futureTradingHours": "GLBX(de=1640;0=-17001600;1=r-17001600d-15551640;7=d-16401555)",   
"product": "/ES"   
},   
"quote": {   
"askPrice": 4694.5,   
"askSize": 113,   
"askTime": 0,   
"bidPrice": 4694.25,   
"bidSize": 57,   
"bidTime": 0,   
"netChange": \-1.5,   
"closePrice": 4696,   
"futurePercentChange": \-0.0003,   
"highPrice": 4701,   
"lastPrice": 4694.5,   
"lastSize": 3,   
"lowPrice": 4679.25,   
"mark": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html10/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"openInterest": 2328678, "openPrice": 4696.5,   
**Logo Developer Portal**   
"quoteTime": 1637168671400,   
"securityStatus": "Unknown", 

Home   
"settleTime": 0, "tick": 0.25,   
"tickAmount": 12.5,   
API Products   
"totalVolume": 550778,   
User Guides }   
},   
"tradeTime": 1637168671399 

"EUR/USD": {   
"assetMainType": "FOREX",   
"symbol": "EUR/USD",   
"ssid": 1,   
"realtime": true,   
"reference": {   
"description": "Euro/USDollar Spot", "exchange": "T",   
"exchangeName": "GFT",   
"isTradable": false,   
"marketMaker": "",   
"product": "",   
"tradingHours": ""   
},   
"quote": {   
"52WeekHigh": 1.135,   
"52WeekLow": 1.1331,   
"askPrice": 1.13456,   
"askSize": 1000000,   
"bidPrice": 1.13434,   
"bidSize": 1000000,   
"netChange": 0.00254,   
"closePrice": 1.13191,   
"highPrice": 1.135,   
"lastPrice": 1.13445,   
"lastSize": 0,   
"lowPrice": 1.1331,   
"mark": 1.13445,   
"openPrice": 1.13324,   
"netPercentChange": 0,   
"quoteTime": 1637236739892,   
"securityStatus": "Unknown",   
"tick": 0,   
"tickAmount": 0,   
"totalVolume": 0, 

}   
}   
}   
"tradeTime": 1637236739892 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
Used to identify an individual request throughout the lifetime of the request and across systems.   
string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

file:///Users/licaris/Downloads/market\_data\_api.html 11/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"/data/attributes/ssids" \]   
**Logo Developer Portal**   
}   
}, 

Home   
{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1", "status": "400",   
API Products   
"title": "Bad Request",   
User Guides   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[ 

}   
{ 

}   
\]   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is   
string   
Example: 977dbd7f-992e 

file:///Users/licaris/Downloads/market\_data\_api.html 12/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type needed. 44d2-a5f4-e213d29c8691   
**Logo Developer Portal** 

Home   
Schwab Resource Version 

This is the requested API version.string Example: 1 

API Products   
~~GET/{symbol~~\_id}/quotes   
Get Quote by single symbol.   
User Guides 

Parameters 

Try it out 

Name Description Symbol of instrument   
symbol\_id \*   
string   
(path) 

fields   
string   
(query) 

Responses   
Example : TSLA 

TSLA 

Request for subset of data by passing coma separated list of root nodes, possible root nodes are quote, fundamental, extended, reference, regular. Sending quote, fundamental in request will return quote and fundamental data in response. Dont send this attribute for full response. 

Default value : all 

quote,reference 

Code Description Links 

200 Quote Response 

Media type   
application/json   
Controls Accept header. Examples   
Search by symbol AAPL 

Example Value 

Schema   
No   
links 

{   
"symbol": "AAPL",   
"empty": false,   
"previousClose": 174.56,   
"previousCloseDate": 1639029600000, "candles": \[   
{   
"open": 175.01,   
"high": 175.15,   
"low": 175.01,   
"close": 175.04,   
"volume": 10719,   
"datetime": 1639137600000   
},   
{   
"open": 175.08,   
"high": 175.09,   
"low": 175.05,   
"close": 175.05,   
"volume": 500,   
"datetime": 1639137660000   
},   
{   
"open": 176.22,   
"high": 176.27,   
"low": 176.22,   
"close": 176.25,   
"volume": 3395,   
"datetime": 1640307300000   
},   
{   
"open": 176.26,   
"high": 176.27,   
"low": 176.26,   
"close": 176.26,   
"volume": 2174,   
"datetime": 1640307360000 

file:///Users/licaris/Downloads/market\_data\_api.html 13/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
}, {   
**Logo Developer Portal**   
"open": 176.26,   
"high": 176.31, 

Home   
"low": 176.26, "close": 176.3, "volume": 15401,   
API Products   
"datetime": 1640307420000   
},   
User Guides   
{   
"open": 176.3,   
"high": 176.3,   
"low": 176.3,   
"close": 176.3,   
"volume": 1700,   
"datetime": 1640307480000   
}, 

}   
{ 

}   
\]   
"open": 176.3,   
"high": 176.5,   
"low": 176.3,   
"close": 176.32,   
"volume": 5941,   
"datetime": 1640307540000 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
Used to identify an individual request throughout the lifetime of the request and across systems.   
string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is   
string   
Example: 977dbd7f-992e 

file:///Users/licaris/Downloads/market\_data\_api.html 14/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type needed. 44d2-a5f4-e213d29c8691   
**Logo Developer Portal** 

Home   
Schwab Resource Version 

This is the requested API version.string Example: 1 

API Products   
~~Error re~~sponse for 401 Unauthorized User Guides   
Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 404 Not Found 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

404   
{ 

}   
\]   
}   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error"   
} 

file:///Users/licaris/Downloads/market\_data\_api.html 15/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\]   
**Schwab**   
}   
**Logo Developer Portal** 

Home   
Headers: 

API Products   
Name Description Type   
User Guides   
~~Schwa~~b-Client   
CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed. 

string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab   
Resource   
Version 

Option Chains 

This is the requested API version.string Example: 1 

Get Option Chains Web Service. 

GET/chains   
Get option chain for an optionable Symbol 

Get Option Chain including information on options contracts associated with each expiration. Parameters 

Try it out 

Name Description Enter one symbol   
symbol \*   
string   
(query) 

contractType string 

(query) 

strikeCount integer 

(query)   
Example : AAPL 

AAPL 

Contract Type 

Available values : CALL, PUT, ALL 

\-- 

The Number of strikes to return above or below the at-the-money price strikeCount   
includeUnderlyingQuote   
Underlying quotes to be included   
boolean   
(query) 

strategy   
string   
(query) 

interval   
number($double) (query) 

strike   
number($double) (query) 

range   
string   
(query) 

fromDate   
string($date) (query) 

toDate   
string($date) (query)   
\-- 

OptionChain strategy. Default is SINGLE. ANALYTICAL allows the use of volatility, underlyingPrice, interestRate, and daysToExpiration params to calculate theoretical values. 

Available values : SINGLE, ANALYTICAL, COVERED, VERTICAL, CALENDAR, STRANGLE, STRADDLE, BUTTERFLY, CONDOR, DIAGONAL, COLLAR, ROLL 

\-- 

Strike interval for spread strategy chains (see strategy param) 

interval 

Strike Price 

strike 

Range(ITM/NTM/OTM etc.) 

range 

From date(pattern: yyyy-MM-dd) 

fromDate 

To date (pattern: yyyy-MM-dd) 

toDate 

volatility Volatility to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param) file:///Users/licaris/Downloads/market\_data\_api.html 16/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Name Description   
Developer Portal   
**Charles**   
number($double) **Schwab**   
(query)   
volatility 

**Logo Developer Portal**   
underlyingPrice number($double)   
Home   
(query)   
API Products 

interestRate   
User Guides   
number($double) (query) 

daysToExpiration integer($int32) (query) 

expMonth   
string   
(query) 

optionType   
string   
(query) 

entitlement   
string   
(query) 

Responses   
Underlying price to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param) 

underlyingPrice 

Interest rate to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param) 

interestRate 

Days to expiration to use in calculations. Applies only to ANALYTICAL strategy chains (see strategy param) 

daysToExpiration 

Expiration month 

Available values : JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, OCT, NOV, DEC, ALL 

\-- 

Option Type 

optionType 

Applicable only if its retail token, entitlement of client PP-PayingPro, NP-NonPro and PN-NonPayingPro Available values : PN, NP, PP 

\-- 

Code Description Links 

200 The Chain for the symbol was returned successfully. 

Media type   
application/json   
Controls Accept header. 

Example Value 

Schema   
No   
links 

{   
"symbol": "string",   
"status": "string",   
"underlying": {   
"ask": 0,   
"askSize": 0,   
"bid": 0,   
"bidSize": 0,   
"change": 0,   
"close": 0,   
"delayed": true,   
"description": "string", "exchangeName": "IND", "fiftyTwoWeekHigh": 0, "fiftyTwoWeekLow": 0, "highPrice": 0,   
"last": 0,   
"lowPrice": 0,   
"mark": 0,   
"markChange": 0,   
"markPercentChange": 0, "openPrice": 0,   
"percentChange": 0,   
"quoteTime": 0,   
"symbol": "string",   
"totalVolume": 0,   
"tradeTime": 0   
},   
"strategy": "SINGLE", "interval": 0,   
"isDelayed": true,   
"isIndex": true,   
"daysToExpiration": 0, "interestRate": 0,   
"underlyingPrice": 0, "volatility": 0,   
"callExpDateMap": {   
"additionalProp1": { "additionalProp1": { "putCall": "PUT", 

file:///Users/licaris/Downloads/market\_data\_api.html 17/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"symbol": "string", "description": "string",   
**LogoDeveloperPortal**   
"exchangeName": "string",   
"bidPrice": 0, 

Home 

API Products User Guides   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0, "tradeTimeInLong": 0, "netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string",   
"daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string",   
"isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0,   
"isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp2": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string",   
"exchangeName": "string",   
"bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0,   
"theoreticalVolatility": 0,   
"isMini": true,   
"isNonStandard": true, 

file:///Users/licaris/Downloads/market\_data\_api.html18/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"optionDeliverablesList": \[ {   
**LogoDeveloperPortal**   
"symbol": "string",   
"assetType": "string", 

Home 

API Products 

}   
\],   
"deliverableUnits": "string", "currencyType": "string" 

User Guides   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string", "isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp3": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

}   
},   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string" 

"additionalProp2": {   
"additionalProp1": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string",   
"exchangeName": "string",   
"bidPrice": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html19/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab** 

"askPrice": 0, "lastPrice": 0,   
**LogoDeveloperPortal**   
"markPrice": 0,   
"bidSize": 0, 

Home 

API Products User Guides   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0, "tradeTimeInLong": 0, "netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string", "isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp2": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{   
"symbol": "string", "assetType": "string", 

file:///Users/licaris/Downloads/market\_data\_api.html20/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"deliverableUnits": "string", "currencyType": "string"   
**LogoDeveloperPortal**   
}   
\], 

Home 

API Products User Guides   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp3": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

}   
},   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string" 

"additionalProp3": {   
"additionalProp1": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string",   
"exchangeName": "string",   
"bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html21/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab** 

"askSize": 0, "lastSize": 0,   
**LogoDeveloperPortal**   
"highPrice": 0,   
"lowPrice": 0, 

Home 

API Products User Guides   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0, "tradeTimeInLong": 0, "netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string", "isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp2": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

file:///Users/licaris/Downloads/market\_data\_api.html22/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"strikePrice": 0,   
"expirationDate": "string",   
**LogoDeveloperPortal**   
"daysToExpiration": 0,   
"expirationType": "M", 

Home 

API Products User Guides   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp3": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

}   
}   
},   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string" 

"putExpDateMap": {   
"additionalProp1": {   
"additionalProp1": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string",   
"exchangeName": "string",   
"bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html23/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab** 

"highPrice": 0, "lowPrice": 0,   
**LogoDeveloperPortal**   
"openPrice": 0,   
"closePrice": 0, 

Home 

API Products User Guides   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0, "tradeTimeInLong": 0, "netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string", "isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp2": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string", 

file:///Users/licaris/Downloads/market\_data\_api.html24/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"daysToExpiration": 0, "expirationType": "M",   
**LogoDeveloperPortal**   
"lastTradingDay": 0,   
"multiplier": 0, 

Home 

API Products User Guides   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp3": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

}   
},   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string" 

"additionalProp2": {   
"additionalProp1": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string",   
"exchangeName": "string",   
"bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html25/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"totalVolume": 0, "tradeDate": 0,   
**LogoDeveloperPortal**   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0, 

Home 

API Products User Guides   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string", "isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp2": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string",   
"daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html26/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"settlementType": "A", "deliverableNote": "string",   
**LogoDeveloperPortal**   
"isIndexOption": true,   
"percentChange": 0, 

Home 

API Products User Guides   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0, "optionRoot": "string"   
},   
"additionalProp3": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

}   
},   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string" 

"additionalProp3": {   
"additionalProp1": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string",   
"exchangeName": "string",   
"bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html27/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles Schwab**   
"netChange": 0, "volatility": 0,   
**LogoDeveloperPortal**   
"delta": 0,   
"gamma": 0, 

Home 

API Products User Guides   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string", "isIndexOption": true,   
"percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true,   
"intrinsicValue": 0,   
"optionRoot": "string"   
},   
"additionalProp2": {   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0,   
"tradeTimeInLong": 0,   
"netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true,   
"theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true,   
"optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

"strikePrice": 0,   
"expirationDate": "string",   
"daysToExpiration": 0,   
"expirationType": "M",   
"lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A",   
"deliverableNote": "string",   
"isIndexOption": true,   
"percentChange": 0, 

file:///Users/licaris/Downloads/market\_data\_api.html28/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"markChange": 0,   
"markPercentChange": 0,   
**Logo Developer Portal**   
"isPennyPilot": true,   
"intrinsicValue": 0, 

Home   
"optionRoot": "string" },   
"additionalProp3": {   
API Products User Guides   
"putCall": "PUT",   
"symbol": "string",   
"description": "string", "exchangeName": "string", "bidPrice": 0,   
"askPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"bidSize": 0,   
"askSize": 0,   
"lastSize": 0,   
"highPrice": 0,   
"lowPrice": 0,   
"openPrice": 0,   
"closePrice": 0,   
"totalVolume": 0,   
"tradeDate": 0,   
"quoteTimeInLong": 0, "tradeTimeInLong": 0, "netChange": 0,   
"volatility": 0,   
"delta": 0,   
"gamma": 0,   
"theta": 0,   
"vega": 0,   
"rho": 0,   
"timeValue": 0,   
"openInterest": 0,   
"isInTheMoney": true, "theoreticalOptionValue": 0, "theoreticalVolatility": 0, "isMini": true,   
"isNonStandard": true, "optionDeliverablesList": \[   
{ 

}   
\],   
"symbol": "string",   
"assetType": "string", "deliverableUnits": "string", "currencyType": "string" 

}   
}   
}   
}   
"strikePrice": 0,   
"expirationDate": "string", "daysToExpiration": 0, "expirationType": "M", "lastTradingDay": 0,   
"multiplier": 0,   
"settlementType": "A", "deliverableNote": "string", "isIndexOption": true, "percentChange": 0,   
"markChange": 0,   
"markPercentChange": 0, "isPennyPilot": true, "intrinsicValue": 0,   
"optionRoot": "string" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
Used to identify an individual request throughout the lifetime of the request and across systems.   
string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[ { 

file:///Users/licaris/Downloads/market\_data\_api.html 29/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
**Logo Developer Portal**   
"title": "Bad Request",   
"detail": "Missing header", 

Home   
"source": { 

}   
API Products   
~~},~~   
{   
"header": "Authorization" 

User Guides   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

404 Error response for 404 Not Found 

Media type   
application/json 

Example Value 

Schema   
No   
links 

file:///Users/licaris/Downloads/market\_data\_api.html 30/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
{   
**Schwab**   
"errors": \[   
**Logo Developer Portal** 

Home   
{   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" }   
API Products   
\]   
User Guides   
~~}~~ 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

500   
{ 

}   
\]   
}   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Option Expiration Chain 

Get Option Expiration Chain Web Service. 

GET/expirationchain   
Get option expiration chain for an optionable symbol 

Get Option Expiration (Series) information for an optionable symbol. Does not include individual options contracts for the underlying. 

Parameters 

Try it out 

Name Description 

Enter one symbol   
symbol \*   
string (query)   
Example : AAPL AAPL 

file:///Users/licaris/Downloads/market\_data\_api.html 31/134  
2/26/26, 6:51 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal Responses   
DeveloperPortal   
**Charles**   
**Schwab**   
CodeDescriptionLinks **LogoDeveloperPortal**   
200TheExpirationChainforthesymbol wasreturnedsuccessfully.   
No 

Home   
Media type   
links 

API Products   
application/json   
Controls Accept header. ~~Exampl~~es   
User Guides 

Get ExpirationChain for AAPL 

Example Value 

Schema 

{   
"expirationList": \[   
{ 

},{ 

},{ 

},{ 

},{ 

},{ 

},{ 

},{ 

},{ 

},{ 

},{ 

},{   
"expirationDate": "2022-01-07", "daysToExpiration": 2,   
"expirationType": "W",   
"standard": true 

"expirationDate": "2022-01-14", "daysToExpiration": 9,   
"expirationType": "W",   
"standard": true 

"expirationDate": "2022-01-21", "daysToExpiration": 16,   
"expirationType": "S",   
"standard": true 

"expirationDate": "2022-01-28", "daysToExpiration": 23,   
"expirationType": "W",   
"standard": true 

"expirationDate": "2022-02-04", "daysToExpiration": 30,   
"expirationType": "W",   
"standard": true 

"expirationDate": "2022-02-11", "daysToExpiration": 37,   
"expirationType": "W",   
"standard": true 

"expirationDate": "2022-02-18", "daysToExpiration": 44,   
"expirationType": "S",   
"standard": true 

"expirationDate": "2022-03-18", "daysToExpiration": 72,   
"expirationType": "S",   
"standard": true 

"expirationDate": "2022-04-14", "daysToExpiration": 99,   
"expirationType": "S",   
"standard": true 

"expirationDate": "2022-05-20", "daysToExpiration": 135, "expirationType": "S",   
"standard": true 

"expirationDate": "2022-06-17", "daysToExpiration": 163, "expirationType": "S",   
"standard": true 

"expirationDate": "2022-07-15", "daysToExpiration": 191, "expirationType": "S",   
"standard": true   
}, 

file:///Users/licaris/Downloads/market\_data\_api.html32/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
{   
"expirationDate": "2022-09-16",   
**Logo Developer Portal**   
"daysToExpiration": 254,   
"expirationType": "S", 

Home   
"standard": true },   
{   
API Products   
"expirationDate": "2023-01-20",   
User Guides   
"daysToExpiration": 380, "expirationType": "S", "standard": true   
},   
{   
"expirationDate": "2023-03-17", "daysToExpiration": 436, "expirationType": "S",   
"standard": true   
},   
{   
"expirationDate": "2023-06-16", "daysToExpiration": 527, "expirationType": "S",   
"standard": true   
},   
{   
"expirationDate": "2023-09-15", "daysToExpiration": 618, "expirationType": "S",   
"standard": true   
}, 

}   
{ 

}   
\]   
"expirationDate": "2024-01-19", "daysToExpiration": 744, "expirationType": "S",   
"standard": true 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
Used to identify an individual request throughout the lifetime of the request and across systems.   
string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

}   
"parameter": "fields" 

file:///Users/licaris/Downloads/market\_data\_api.html 33/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
\]   
} 

**Logo Developer Portal** }   
Home   
Headers: 

API Products 

Name Description Type User Guides 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 404 Not Found 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

404   
{ 

}   
\]   
}   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value   
No   
links 

file:///Users/licaris/Downloads/market\_data\_api.html 34/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab** 

Schema 

**Logo Developer Portal**   
{   
"errors": \[   
Home   
{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",   
API Products   
"status": 500,   
User Guides   
"title": "Internal Server Error" 

\]   
}   
} 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab   
Resource   
Version 

PriceHistory 

This is the requested API version.string Example: 1 

Get Price History Web Service. 

GET/pricehistory   
Get PriceHistory for a single symbol and date ranges. 

Get historical Open, High, Low, Close, and Volume for a given frequency (i.e. aggregation). Frequency available is dependent on periodType selected. The datetime format is in EPOCH milliseconds. 

Parameters 

Try it out 

Name Description 

The Equity symbol used to look up price history   
symbol \*   
string   
(query) 

periodType string 

(query) 

period   
integer($int32) (query) 

frequencyType string 

(query)   
Example : AAPL 

AAPL 

The chart period being requested. 

Available values : day, month, year, ytd 

\-- 

The number of chart period types. 

If the periodType is   
• day \- valid values are 1, 2, 3, 4, 5, 10 • month \- valid values are 1, 2, 3, 6 

• year \- valid values are 1, 2, 3, 5, 10, 15, 20 • ytd \- valid values are 1 

If the period is not specified and the periodType is • day \- default period is 10\. 

• month \- default period is 1\.   
• year \- default period is 1\.   
• ytd \- default period is 1\. 

period 

The time frequencyType 

If the periodType is   
• day \- valid value is minute   
• month \- valid values are daily, weekly • year \- valid values are daily, weekly, monthly 

file:///Users/licaris/Downloads/market\_data\_api.html 35/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Name Description   
Developer Portal   
**Charles Schwab**   
• ytd \- valid values are daily, weekly 

**Logo Developer Portal**   
If frequencyType is not specified, default value depends on the periodType • day \- defaulted to minute.   
Home 

API Products User Guides 

frequency   
integer($int32) (query) 

startDate   
integer($int64) (query) 

endDate   
integer($int64) (query)   
• month \- defaulted to weekly.   
• year \- defaulted to monthly.   
• ytd \- defaulted to weekly. 

Available values : minute, daily, weekly, monthly 

\-- 

The time frequency duration 

If the frequencyType is   
• minute \- valid values are 1, 5, 10, 15, 30   
• daily \- valid value is 1   
• weekly \- valid value is 1   
• monthly \- valid value is 1 

If frequency is not specified, default value is 1 

frequency 

The start date, Time in milliseconds since the UNIX epoch eg 1451624400000 If not specified startDate will be (endDate \- period) excluding weekends and holidays. 

startDate 

The end date, Time in milliseconds since the UNIX epoch eg 1451624400000 If not specified, the endDate will default to the market close of previous business day. 

endDate 

needExtendedHoursData   
Need extended hours data   
boolean   
(query) 

needPreviousClose boolean 

(query) 

Responses 

\-- 

Need previous close price/date \-- 

Code Description Links 

200 Get all candles for given date range 

Media type   
application/json   
Controls Accept header.   
Examples   
Search by symbol AAPL 

Example Value 

Schema   
No   
links 

{   
"symbol": "AAPL",   
"empty": false,   
"previousClose": 174.56,   
"previousCloseDate": 1639029600000, "candles": \[   
{   
"open": 175.01,   
"high": 175.15,   
"low": 175.01,   
"close": 175.04,   
"volume": 10719,   
"datetime": 1639137600000   
},   
{   
"open": 175.08,   
"high": 175.09,   
"low": 175.05,   
"close": 175.05,   
"volume": 500,   
"datetime": 1639137660000   
},   
{   
"open": 176.22, "high": 176.27, "low": 176.22, 

file:///Users/licaris/Downloads/market\_data\_api.html 36/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"close": 176.25, "volume": 3395,   
**Logo Developer Portal**   
"datetime": 1640307300000   
}, 

Home   
{   
"open": 176.26, "high": 176.27,   
API Products   
"low": 176.26,   
User Guides   
"close": 176.26,   
"volume": 2174,   
"datetime": 1640307360000   
},   
{   
"open": 176.26,   
"high": 176.31,   
"low": 176.26,   
"close": 176.3,   
"volume": 15401,   
"datetime": 1640307420000   
},   
{   
"open": 176.3,   
"high": 176.3,   
"low": 176.3,   
"close": 176.3,   
"volume": 1700,   
"datetime": 1640307480000   
}, 

}   
{ 

}   
\]   
"open": 176.3,   
"high": 176.5,   
"low": 176.3,   
"close": 176.32,   
"volume": 5941,   
"datetime": 1640307540000 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

file:///Users/licaris/Downloads/market\_data\_api.html 37/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
Name Description Type   
**Schwab**   
This Correlation ID is unique to the operation. The GUID that is 

string   
**Logo Developer Portal** 

Home   
Schwab-Client CorrelId 

Schwab   
generated can be used to track an individual service call if support is needed. 

Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

API Products   
~~Resour~~ce   
Version   
User Guides   
This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 404 Not Found 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

404   
{ 

}   
\]   
}   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500, 

file:///Users/licaris/Downloads/market\_data\_api.html 38/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"title": "Internal Server Error" }   
**Logo Developer Portal** 

Home   
}   
\] 

Headers:   
API Products 

User Guides   
Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab   
Resource   
Version 

Movers 

This is the requested API version.string Example: 1 

Get Movers Web Service. 

GET/movers/{symbol\_id}   
Get Movers for a specific index. 

Get a list of top 10 securities movement for a specific index. Parameters 

Try it out 

Name Description Index Symbol 

symbol\_id \* string 

(path) 

sort   
string   
(query) 

frequency   
Available values : $DJI, $COMPX, $SPX, NYSE, NASDAQ, OTCBB, INDEX\_ALL, EQUITY\_ALL, OPTION\_ALL, OPTION\_PUT, OPTION\_CALL 

Example : $DJI 

$DJI 

Sort by a particular attribute 

Available values : VOLUME, TRADES, PERCENT\_CHANGE\_UP, PERCENT\_CHANGE\_DOWN Example : VOLUME 

\-- 

To return movers with the specified directions of up or down 

Available values : 0, 1, 5, 10, 30, 60   
integer($int32)   
(query) 

Responses   
Default value : 0 \-- 

Code Description Links 

200 Analytics for the symbol was returned successfully. 

Media type   
application/json   
Controls Accept header.   
Examples   
Search by "$DJI" 

Example Value 

Schema   
No   
links 

{   
"screeners": \[ 

file:///Users/licaris/Downloads/market\_data\_api.html 39/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
{   
"change": 10,   
**Logo Developer Portal**   
"description": "Dow jones",   
"direction": "up", 

Home   
"last": 100,   
"symbol": "$DJI", "totalVolume": 100   
API Products   
~~},~~   
{   
User Guides   
"change": 10,   
"description": "Dow jones", "direction": "up",   
"last": 100,   
"symbol": "$DJI",   
"totalVolume": 100   
}, 

}   
{ 

}   
\]   
"change": 10,   
"description": "Dow jones", "direction": "up",   
"last": 100,   
"symbol": "$DJI",   
"totalVolume": 100 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
Used to identify an individual request throughout the lifetime of the request and across systems.   
string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is   
string   
Example: 977dbd7f-992e 

file:///Users/licaris/Downloads/market\_data\_api.html 40/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type needed. 44d2-a5f4-e213d29c8691   
**Logo Developer Portal** 

Home   
Schwab Resource Version 

This is the requested API version.string Example: 1 

API Products   
~~Error re~~sponse for 401 Unauthorized User Guides   
Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 404 Not Found 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

404   
{ 

}   
\]   
}   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error"   
} 

file:///Users/licaris/Downloads/market\_data\_api.html 41/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\]   
**Schwab**   
}   
**Logo Developer Portal** 

Home   
Headers: 

API Products   
Name Description Type   
User Guides   
~~Schwa~~b-Client   
CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed. 

string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab   
Resource   
Version 

MarketHours 

This is the requested API version.string Example: 1 

Get MarketHours Web Service. 

GET/markets   
Get Market Hours for different markets. 

Get Market Hours for dates in the future across different markets. Parameters 

Try it out 

Name Description List of markets 

markets \* array\[string\] (query) 

date   
Available values : equity, option, bond, future, forex 

equity 

option 

bond   
future 

Valid date range is from currentdate to 1 year from today. It will default to current day if not entered. Date format:YYYY-MM-DD   
string($date) (query) 

Responses 

date 

Code Description Links 

200 OK 

Media type   
application/json   
Controls Accept header.   
Examples   
Get getMarketHours for EQUITY and OPTION 

Example Value 

Schema   
No   
links 

{   
"equity": {   
"EQ": {   
"date": "2022-04-14", "marketType": "EQUITY", "product": "EQ",   
"productName": "equity", "isOpen": true,   
"sessionHours": {   
"preMarket": \[   
{ 

}   
\],   
"start": "2022-04-14T07:00:00-04:00", "end": "2022-04-14T09:30:00-04:00" 

"regularMarket": \[ 

file:///Users/licaris/Downloads/market\_data\_api.html 42/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
{   
"start": "2022-04-14T09:30:00-04:00",   
**Logo Developer Portal** 

Home 

}   
\],   
"end": "2022-04-14T16:00:00-04:00" 

"postMarket": \[ 

API Products User Guides 

}   
}   
},   
{ 

}   
\] 

"start": "2022-04-14T16:00:00-04:00", "end": "2022-04-14T20:00:00-04:00" 

"option": {   
"EQO": {   
"date": "2022-04-14",   
"marketType": "OPTION",   
"product": "EQO",   
"productName": "equity option", "isOpen": true,   
"sessionHours": {   
"regularMarket": \[ 

}   
},   
{ 

}   
\]   
"start": "2022-04-14T09:30:00-04:00", "end": "2022-04-14T16:00:00-04:00" 

"IND": {   
"date": "2022-04-14",   
"marketType": "OPTION",   
"product": "IND",   
"productName": "index option", "isOpen": true,   
"sessionHours": {   
"regularMarket": \[ 

}   
}   
}   
}   
{ 

}   
\] 

"start": "2022-04-14T09:30:00-04:00", "end": "2022-04-14T16:15:00-04:00" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The generated GUID can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e-44d2- a5f4-e213d29c8691 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

\]   
}   
"/data/attributes/ssids" 

file:///Users/licaris/Downloads/market\_data\_api.html 43/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
}, {   
**Logo Developer Portal**   
"id": "28485414-290f-42e2-992b-58ea3e3203b1", "status": "400", 

Home   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": {   
API Products   
"parameter": "fields"   
User Guides   
} 

\]   
}   
} 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[ 

}   
{ 

}   
\]   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource   
This is the requested API version. string Example: 1 

file:///Users/licaris/Downloads/market\_data\_api.html 44/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type 

Version   
**Logo Developer Portal**   
GET/markets/{market\_id}   
Get Market Hours for a single market.   
Home 

API Products 

User Guides   
Get Market Hours for dates in the future for a single market. Parameters 

Try it out 

Name Description 

market\_id \* string 

(path) 

date   
market id 

Available values : equity, option, bond, future, forex 

equity 

Valid date range is from currentdate to 1 year from today. It will default to current day if not entered. Date format:YYYY-MM-DD   
string($date) (query) 

Responses 

date 

Code Description Links 

200 OK 

Media type   
application/json   
Controls Accept header.   
Examples   
Get market hours for equity market 

Example Value 

Schema   
No   
links 

{   
"equity": {   
"EQ": {   
"date": "2022-04-14", "marketType": "EQUITY", "exchange": "NULL",   
"category": "NULL",   
"product": "EQ",   
"productName": "equity", "isOpen": true,   
"sessionHours": {   
"preMarket": \[   
{ 

}   
\],   
"start": "2022-04-14T07:00:00-04:00", "end": "2022-04-14T09:30:00-04:00" 

"regularMarket": \[   
{ 

}   
\],   
"start": "2022-04-14T09:30:00-04:00", "end": "2022-04-14T16:00:00-04:00" 

"postMarket": \[ 

}   
}   
}   
}   
{ 

}   
\] 

"start": "2022-04-14T16:00:00-04:00", "end": "2022-04-14T20:00:00-04:00" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The generated GUID can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e-44d2- 

file:///Users/licaris/Downloads/market\_data\_api.html 45/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab** 

Name Description Type a5f4-e213d29c8691   
**Logo Developer Portal**   
Error response for generic client error 400 

Home   
Media type   
API Products   
application/json 

User Guides   
Example Value 

Schema 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

{ 

400   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.", "source": {   
"pointer": \[   
"/data/attributes/symbols",   
"/data/attributes/cusips", 

No   
links 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

401 Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[ 

}   
{ 

}   
\]   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is   
string   
Example: 977dbd7f-992e 

file:///Users/licaris/Downloads/market\_data\_api.html 46/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type needed. 44d2-a5f4-e213d29c8691   
**Logo Developer Portal** 

Home   
Schwab Resource Version 

This is the requested API version.string Example: 1 

API Products   
~~Error re~~sponse for 404 Not Found User Guides   
Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

404   
{ 

}   
\]   
}   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

500   
{ 

}   
\]   
}   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab   
Resource   
Version 

Instruments 

This is the requested API version.string Example: 1 

Get Instruments Web Service. 

GET/instruments   
Get Instruments by symbols and projections. 

file:///Users/licaris/Downloads/market\_data\_api.html 47/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Get Instruments details by using different projections. Get more specific fundamental instrument data by using fundamental as Developer Portal   
**Charles**   
the projection.   
**Schwab**   
**Logo Developer Portal**   
Parameters 

Home 

Try it out   
API Products 

Name Description   
User Guides 

symbol \* string 

(query)   
symbol of a security 

symbol 

search by   
projection \*   
string (query)   
Available values : symbol-search, symbol-regex, desc-search, desc-regex, search, fundamental symbol-search 

Responses 

Code Description Links OK 

Media type   
application/json   
Controls Accept header.   
Examples   
symbol=AAPL,BAC\&projection=symbol-search 

Example Value 

Schema 

{   
"instruments": \[   
{   
"cusip": "037833100", "symbol": "AAPL",   
"description": "Apple Inc", "exchange": "NASDAQ", "assetType": "EQUITY" 

No   
200   
}, 

links 

}   
{ 

}   
\]   
"cusip": "060505104",   
"symbol": "BAC",   
"description": "Bank Of America Corp", "exchange": "NYSE",   
"assetType": "EQUITY" 

Headers: 

Name Description Type 

Schwab-Resource Version 

Schwab-Client CorrelId   
Used to identify desired and returned version of an API resource 

Used to identify an individual request throughout the lifetime of the request and across systems.   
integer   
Example: 3 

string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": { 

}   
},   
"header": "Authorization" 

file:///Users/licaris/Downloads/market\_data\_api.html 48/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
{   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca",   
**Logo Developer Portal**   
"status": "400",   
"title": "Bad Request", 

Home   
"detail": "Search combination should have min of 1.", "source": { 

API Products User Guides   
"pointer": \[   
"/data/attributes/symbols", "/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

500 Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[ 

}   
{ 

}   
\]   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error" 

file:///Users/licaris/Downloads/market\_data\_api.html 49/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
Headers:   
**Schwab**   
**Logo Developer Portal**   
Name Description Type 

Home   
Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is   
string   
Example: 977dbd7f-992e   
API Products User Guides   
needed. 

44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

GET/instruments/{cusip\_id}   
Get Instrument by specific cusip 

Get basic instrument details by cusip Parameters 

Try it out 

Name Description 

cusip\_id \*   
cusip of a security   
string (path) 

cusip\_id 

Responses 

Code Description Links OK 

Media type   
application/json   
Controls Accept header.   
Examples   
Get getinstruments for cusip 

Example Value 

Schema 

200   
{ }   
"cusip": "037833100", "symbol": "AAPL",   
"description": "Apple Inc", "exchange": "NASDAQ", "assetType": "EQUITY" 

No   
links 

Headers: 

Name Description Type 

Schwab-Resource Version 

Schwab-Client CorrelId   
Used to identify desired and returned version of an API resource 

Used to identify an individual request throughout the lifetime of the request and across systems.   
integer   
Example: 3 

string   
Example: 0a7f446a-7d74-49c8-a1e5- ca8ed59a3386 

400 Error response for generic client error 400 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"id": "6808262e-52bb-4421-9d31-6c0e762e7dd5", "status": "400",   
"title": "Bad Request",   
"detail": "Missing header",   
"source": {   
"header": "Authorization" 

file:///Users/licaris/Downloads/market\_data\_api.html 50/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
}   
},   
**Logo Developer Portal**   
{ 

Home   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": "400",   
"title": "Bad Request",   
"detail": "Search combination should have min of 1.",   
API Products   
"source": {   
User Guides   
"pointer": \[   
"/data/attributes/symbols", "/data/attributes/cusips", 

\]   
}   
},   
"/data/attributes/ssids" 

{   
"id": "28485414-290f-42e2-992b-58ea3e3203b1",   
"status": "400",   
"title": "Bad Request",   
"detail": "valid fields should be any of all,fundamental,reference,extended,quote,regular or empty value", "source": { 

\]   
}   
}   
}   
"parameter": "fields" 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 401 Unauthorized 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

401   
{ 

}   
\]   
}   
"status": 401,   
"title": "Unauthorized",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

404 Error response for 404 Not Found 

Media type   
application/json 

Example Value 

Schema   
No   
links 

{   
"errors": \[   
{   
"status": 404,   
"title": "Not Found",   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca" } 

file:///Users/licaris/Downloads/market\_data\_api.html 51/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\]   
**Schwab**   
}   
**Logo Developer Portal** 

Home   
Headers: 

API Products   
Name Description Type   
User Guides   
~~Schwa~~b-Client   
CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed. 

string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab Resource Version 

This is the requested API version.string Example: 1 

Error response for 500 Internal Server Error 

Media type   
application/json 

Example Value 

Schema 

{   
"errors": \[ 

500   
{ 

}   
\]   
}   
"id": "0be22ae7-efdf-44d9-99f4-f138049d76ca", "status": 500,   
"title": "Internal Server Error" 

No   
links 

Headers: 

Name Description Type 

Schwab-Client CorrelId   
This Correlation ID is unique to the operation. The GUID that is generated can be used to track an individual service call if support is needed.   
string   
Example: 977dbd7f-992e 44d2-a5f4-e213d29c8691 

Schwab   
Resource   
Version 

Schemas 

Bond { 

cusip string symbol string description string exchange string 

This is the requested API version.string Example: 1 

assetType   
stringEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

bondFactor string bondMultiplier string bondPrice number string 

type }   
writeOnly: trueEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

FundamentalInst { 

symbol string 

high52 number($double) 

low52 number($double) 

dividendAmount number($double) 

dividendYield number($double) 

dividendDate string 

peRatio number($double) 

pegRatio number($double) 

file:///Users/licaris/Downloads/market\_data\_api.html 52/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal pbRatio number($double)   
Developer Portal   
**Charles**   
prRatio number($double)   
**Schwab**   
pcfRatio number($double)   
**Logo Developer Portal**   
grossMarginTTM number($double) 

grossMarginMRQ number($double)   
Home   
netProfitMarginTTM number($double)   
API Products   
netProfitMarginMRQ number($double)   
User Guides   
operatingMarginTTM number($double) 

operatingMarginMRQ number($double) 

returnOnEquity number($double) 

returnOnAssets number($double) 

returnOnInvestment number($double) 

quickRatio number($double) 

currentRatio number($double) 

interestCoverage number($double) 

totalDebtToCapital number($double) 

ltDebtToEquity number($double) 

totalDebtToEquity number($double) 

epsTTM number($double) 

epsChangePercentTTM number($double) 

epsChangeYear number($double) 

epsChange number($double) 

revChangeYear number($double) 

revChangeTTM number($double) 

revChangeIn number($double) 

sharesOutstanding number($double) 

marketCapFloat number($double) 

marketCap number($double) 

bookValuePerShare number($double) 

shortIntToFloat number($double) 

shortIntDayToCover number($double) 

divGrowthRate3Year number($double) 

dividendPayAmount number($double) 

dividendPayDate string 

beta number($double) 

vol1DayAvg number($double) 

vol10DayAvg number($double) 

vol3MonthAvg number($double) 

avg10DaysVolume integer($int64) 

avg1DayVolume integer($int64) 

avg3MonthVolume integer($int64) 

declarationDate string 

dividendFreq integer($int32) 

eps number($double) 

corpactionDate string 

dtnVolume integer($int64) 

nextDividendPayDate string 

nextDividendDate string 

fundLeverageFactor number($double) 

fundStrategy string 

} 

Instrument { 

cusip string 

symbol string 

description string 

exchange string 

stringEnum:   
assetType type   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

string   
writeOnly: trueEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

file:///Users/licaris/Downloads/market\_data\_api.html 53/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal }   
Developer Portal   
**Charles**   
InstrumentResponse {   
**Schwab**   
**Logo Developer Portal**   
cusip string 

symbol string 

Home   
description string 

exchange string   
API Products 

User Guides assetType   
stringEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

bondFactor string 

bondMultiplier string 

bondPrice number 

fundamental \#/components/schemas/FundamentalInstFundamentalInst { 

symbol string 

high52 number($double) 

low52 number($double) 

dividendAmount number($double) 

dividendYield number($double) 

dividendDate string 

peRatio number($double) 

pegRatio number($double) 

pbRatio number($double) 

prRatio number($double) 

pcfRatio number($double) 

grossMarginTTM number($double) 

grossMarginMRQ number($double) 

netProfitMarginTTM number($double) 

netProfitMarginMRQ number($double) 

operatingMarginTTM number($double) 

operatingMarginMRQ number($double) 

returnOnEquity number($double) 

returnOnAssets number($double) 

returnOnInvestment number($double) 

quickRatio number($double) 

currentRatio number($double) 

interestCoverage number($double) 

totalDebtToCapital number($double) 

ltDebtToEquity number($double) 

totalDebtToEquity number($double) 

epsTTM number($double) 

epsChangePercentTTM number($double) 

epsChangeYear number($double) 

epsChange number($double) 

revChangeYear number($double) 

revChangeTTM number($double) 

revChangeIn number($double) 

sharesOutstanding number($double) 

marketCapFloat number($double) 

marketCap number($double) 

bookValuePerShare number($double) 

shortIntToFloat number($double) 

shortIntDayToCover number($double) 

divGrowthRate3Year number($double) 

dividendPayAmount number($double) 

dividendPayDate string 

beta number($double) 

vol1DayAvg number($double) 

vol10DayAvg number($double) 

vol3MonthAvg number($double) 

avg10DaysVolume integer($int64) 

avg1DayVolume integer($int64) 

avg3MonthVolume integer($int64) 

declarationDate string 

file:///Users/licaris/Downloads/market\_data\_api.html 54/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
dividendFreq integer($int32) eps number($double) corpactionDate string   
**Logo Developer Portal**   
dtnVolume integer($int64) 

Home 

API Products User Guides 

instrumentInfo   
nextDividendPayDate string 

nextDividendDate string 

fundLeverageFactor number($double) fundStrategy string   
} 

\#/components/schemas/InstrumentInstrument { cusip string   
symbol string 

description string 

exchange string 

stringEnum:   
assetType 

type 

}   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

string   
writeOnly: trueEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

\#/components/schemas/BondBond { cusip string   
symbol string 

description string 

exchange string 

stringEnum:   
assetType 

bondInstrumentInfo   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

bondFactor string bondMultiplier string bondPrice number string 

type 

} 

string   
writeOnly: trueEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

type 

} 

Hours {   
writeOnly: trueEnum:   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

date string stringEnum:   
marketType   
\[ BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN \] 

exchange string 

category string 

product string 

productName string 

isOpen boolean 

{ 

\[ \#/components/schemas/IntervalInterval { 

sessionHours 

\< \* \>:   
start string end string }\] 

} 

} 

Interval { 

start string 

end string 

} 

file:///Users/licaris/Downloads/market\_data\_api.html 55/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Screener {   
Developer Portal   
**Charles**   
description: Security info of most moved with in an index   
**Schwab**   
**Logo Developer Portal**   
number($double)   
change 

Home   
percent or value changed, by default its percent changed string   
API Products   
description   
Name of security   
User Guides 

directionstringEnum:   
\[ up, down \] 

number($double)   
last   
what was last quoted price 

string   
symbol   
schwab security symbol 

totalVolume integer($int64) 

} 

Candle { 

close number($double) 

datetime integer($int64) 

datetimeISO8601 string($yyyy-MM-dd) 

high number($double) 

low number($double) 

open number($double) 

volume integer($int64) 

} 

CandleList { 

\[ \#/components/schemas/CandleCandle { 

close number($double) 

datetime integer($int64) 

datetimeISO8601 string($yyyy-MM-dd) 

candles   
high number($double) low number($double) open number($double) volume integer($int64)   
}\] 

empty boolean 

previousClose number($double) 

previousCloseDate integer($int64) 

previousCloseDateISO8601 string($yyyy-MM-dd) 

symbol string 

} 

EquityResponse { 

description: Quote info of Equity security 

AssetMainTypestring 

Instrument's asset type   
assetMainType 

Enum:   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] EquityAssetSubTypestring   
nullable: true 

assetSubType 

ssid 

symbol   
Asset Sub Type (only there if applicable) 

Enum:   
\[ COE, PRF, ADR, GDR, CEF, ETF, ETN, UIT, WAR, RGT, \] integer($int64)   
example: 1234567890 

SSID of instrument 

string   
example: AAPL 

Symbol of instrument 

file:///Users/licaris/Downloads/market\_data\_api.html 56/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
realtime   
**Schwab**   
boolean   
example: true 

**Logo Developer Portal**   
is quote realtime 

QuoteTypestring   
Home 

API Products ~~quoteType~~ User Guides   
nullable: true 

NBBO \- realtime, NFL \- Non-fee liable quote. 

Enum:   
\[ NBBO, NFL, \] 

\#/components/schemas/ExtendedMarketExtendedMarket { description: Quote data for extended hours number($double) 

extended   
askPrice askSize bidPrice bidSize 

lastPrice lastSize mark 

quoteTime   
example: 124.85 

Extended market ask price 

integer($int32)   
example: 51771 

Extended market ask size 

number($double)   
example: 124.85 

Extended market bid price 

integer($int32)   
example: 51771 

Extended market bid size 

number($double)   
example: 124.85 

Extended market last price 

integer($int32)   
example: 51771 

Regular market last size 

number($double)   
example: 1.1246 

mark price 

integer($int64)   
example: 1621368000400 

Extended market quote time in milliseconds since Epoch number($int64)   
example: 12345   
totalVolume 

Total volume 

integer($int64) 

tradeTime }   
example: 1621368000400 

Extended market trade time in milliseconds since Epoch 

fundamental \#/components/schemas/FundamentalFundamental { description: Fundamentals of a security   
number($double)   
avg10DaysVolume   
Average 10 day volume 

number($double)   
avg1YearVolume   
Average 1 day volume 

string($date-time)   
example: 2021-04-28T00:00:00Z   
declarationDate divAmount   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Declaration date in yyyy-mm-ddThh:mm:ssZ number($double)   
example: 0.88 

Dividend Amount 

divExDate string($yyyy-MM-dd'T'HH:mm:ssZ)   
example: 2021-05-07T00:00:00Z 

file:///Users/licaris/Downloads/market\_data\_api.html 57/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal**   
Dividend date in yyyy-mm-ddThh:mm:ssZ 

DivFreqinteger   
nullable: true 

Dividend frequency 1 – once a year or annually 2 – 2x a year or semi-annualy 3 \- 3x a year   
Home 

API Products User Guides   
divFreq 

divPayAmount divPayDate 

divYield 

eps   
(ex. ARCO, EBRPF) 4 – 4x a year or quarterly 6 \- 6x per yr or every other month 11 – 11x a year (ex. FBND, FCOR) 12 – 12x a year or monthly 

Enum: 

\[ 1, 2, 3, 4, 6, 11, 12, \] 

number($double)   
example: 0.22 

Dividend Pay Amount 

string($date-time)   
example: 2021-05-13T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Dividend pay date in yyyy-mm-ddThh:mm:ssZ 

number($double)   
example: 0.7 

Dividend yield 

number($double)   
example: 4.45645 

Earnings per Share 

number($double)   
example: \-1   
fundLeverageFactor 

Fund Leverage Factor \+ \> 0 \<- 

FundStrategystring   
nullable: true 

fundStrategy 

nextDivExDate 

nextDivPayDate 

peRatio 

}   
FundStrategy "A" \- Active "L" \- Leveraged "P" \- Passive "Q" \- Quantitative "S" \- Short 

Enum:   
\[ A, L, P, Q, S, \] 

string($date-time)   
example: 2021-02-12T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Next Dividend date 

string($date-time)   
example: 2021-02-12T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Next Dividend pay date 

number($double)   
example: 28.599 

P/E Ratio 

quote \#/components/schemas/QuoteEquityQuoteEquity { description: Quote data of Equity security number($double) 

52WeekHigh 52WeekLow askMICId 

askPrice   
example: 145.09 

Higest price traded in the past 12 months, or 52 weeks number($double)   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks string   
example: XNYS 

ask MIC code 

number($double)   
example: 124.63 

Current Best Ask Price 

askSize integer($int32)   
example: 700 

file:///Users/licaris/Downloads/market\_data\_api.html 58/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

askTime   
Number of shares for ask integer($int64)   
example: 1621376892336   
**Logo Developer Portal** Home 

Last ask time in milliseconds since Epoch string   
API Products User Guides   
bidMICId bidPrice bidSize 

bidTime closePrice highPrice lastMICId   
example: XNYS 

bid MIC code 

number($double)   
example: 124.6 

Current Best Bid Price 

integer($int32)   
example: 300 

Number of shares for bid 

integer($int64)   
example: 1621376892336 

Last bid time in milliseconds since Epoch number($double)   
example: 126.27 

Previous day's closing price 

number($double)   
example: 126.99 

Day's high trade price 

string   
example: XNYS 

Last MIC Code 

lastPricenumber($double) example: 122.3   
integer($int32) 

lastSize 

lowPrice 

mark 

markChange   
example: 100 

Number of shares traded with last trade number($double) 

Day's low trade price 

number($double)   
example: 52.93 

Mark price 

number($double)   
example: \-0.01 

Mark Price change 

number($double)   
example: \-0.0189   
markPercentChange 

Mark Price percent change 

number($double) 

netChange 

netPercentChange openPrice 

quoteTime 

securityStatus   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756 

Net Percentage Change 

number($double)   
example: 52.8 

Price at market open 

integer($int64)   
example: 1621376892336 

Last quote time in milliseconds since Epoch string   
example: Normal 

file:///Users/licaris/Downloads/market\_data\_api.html 59/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

totalVolume   
Status of security integer($int64) example: 20171188   
**Logo Developer Portal** Home   
Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
API Products User Guides   
tradeTime 

volatility }   
example: 1621376731304 

Last trade time in milliseconds since Epoch number($double)   
example: 0.0094 

Option Risk/Volatility Measurement 

\#/components/schemas/ReferenceEquityReferenceEquity { description: Reference data of Equity security string 

reference   
cusip 

description 

exchange 

exchangeName fsiDesc 

htbQuantity htbRate   
example: A23456789 

CUSIP of Instrument 

string   
example: Apple Inc. \- Common Stock 

Description of Instrument 

string   
example: q 

Exchange Code 

string 

Exchange Name 

string   
maxLength: 50 

FSI Desc 

integer($int32)   
example: 100 

Hard to borrow quantity. 

number($double)   
example: 4.5 

Hard to borrow rate. 

boolean   
example: false   
isHardToBorrow 

is Hard to borrow security. 

boolean 

isShortable 

otcMarketTier }   
example: false 

is shortable security. string   
maxLength: 10 

OTC Market Tier 

regular \#/components/schemas/RegularMarketRegularMarket { description: Market info of security number($double) 

regularMarketLastPrice regularMarketLastSize regularMarketNetChange   
example: 124.85 

Regular market last price integer($int32)   
example: 51771 

Regular market last size number($double)   
example: \-1.42 

Regular market net change 

regularMarketPercentChange number($double)   
example: \-1.1246 

file:///Users/licaris/Downloads/market\_data\_api.html 60/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
regularMarketTradeTime   
Regular market percent change integer($int64)   
example: 1621368000400   
**Logo Developer Portal**   
Regular market trade time in milliseconds since Epoch   
Home 

API Products   
} 

User Guides QuoteError {   
} 

description: Partial or Custom errors per request \[ 

invalidCusips invalidSSIDs   
list of invalid cusips from request 

string\] 

\[ 

list of invalid SSIDs from request 

integer($int64)\] 

\[ 

invalidSymbols   
list of invalid symbols from request 

string\] 

} 

ExtendedMarket { 

description: Quote data for extended hours number($double) 

askPrice askSize bidPrice bidSize 

lastPrice lastSize mark 

quoteTime   
example: 124.85 

Extended market ask price 

integer($int32)   
example: 51771 

Extended market ask size 

number($double)   
example: 124.85 

Extended market bid price 

integer($int32)   
example: 51771 

Extended market bid size 

number($double)   
example: 124.85 

Extended market last price 

integer($int32)   
example: 51771 

Regular market last size 

number($double)   
example: 1.1246 

mark price 

integer($int64)   
example: 1621368000400 

Extended market quote time in milliseconds since Epoch number($int64)   
example: 12345   
totalVolume 

Total volume 

integer($int64) 

tradeTime }   
example: 1621368000400 

Extended market trade time in milliseconds since Epoch 

ForexResponse { 

description: Quote info of Forex security 

file:///Users/licaris/Downloads/market\_data\_api.html 61/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
AssetMainTypestring Instrument's asset type   
assetMainType   
**Logo Developer Portal** Enum: 

Home 

API Products ssid   
User Guides symbol 

realtime   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] integer($int64)   
example: 1234567890 

SSID of instrument 

string   
example: AAPL 

Symbol of instrument 

boolean   
example: true 

is quote realtime 

quote \#/components/schemas/QuoteForexQuoteForex { description: Quote data of Forex security number($double) 

52WeekHigh 52WeekLow askPrice 

askSize 

bidPrice 

bidSize 

closePrice highPrice   
example: 145.09 

Higest price traded in the past 12 months, or 52 weeks number($double)   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks number($double)   
example: 124.63 

Current Best Ask Price 

integer($int32)   
example: 700 

Number of shares for ask 

number($double)   
example: 124.6 

Current Best Bid Price 

integer($int32)   
example: 300 

Number of shares for bid 

number($double)   
example: 126.27 

Previous day's closing price 

number($double)   
example: 126.99 

Day's high trade price 

lastPricenumber($double) example: 122.3   
integer($int32) 

lastSize 

lowPrice mark 

netChange   
example: 100 

Number of shares traded with last trade number($double)   
example: 52.74 

Day's low trade price 

number($double)   
example: 52.93 

Mark price 

number($double)   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756   
netPercentChange 

file:///Users/licaris/Downloads/market\_data\_api.html 62/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

openPrice   
Net Percentage Change number($double)   
example: 52.8   
**Logo Developer Portal** Home 

Price at market open integer($int64)   
API Products User Guides   
quoteTime 

securityStatus tick 

tickAmount totalVolume 

tradeTime 

}   
example: 1621376892336 

Last quote time in milliseconds since Epoch 

string   
example: Normal 

Status of security 

number($double)   
example: 0 

Tick Price 

number($double)   
example: 0 

Tick Amount 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

\#/components/schemas/ReferenceForexReferenceForex { description: Reference data of Forex security string 

description exchange   
example: Euro/USDollar Spot 

Description of Instrument string   
example: q 

Exchange Code 

string 

reference   
exchangeName 

Exchange Name 

boolean 

isTradable 

marketMaker 

product 

tradingHours 

} 

} 

Fundamental {   
example: true 

is FOREX tradable string 

Market marker string   
example: null 

Product name string 

Trading hours 

description: Fundamentals of a security number($double)   
avg10DaysVolume   
Average 10 day volume 

number($double)   
avg1YearVolume   
Average 1 day volume 

string($date-time)   
example: 2021-04-28T00:00:00Z   
declarationDate   
pattern: yyyy-MM-dd'T'HH:mm:ssZ Declaration date in yyyy-mm-ddThh:mm:ssZ 

file:///Users/licaris/Downloads/market\_data\_api.html 63/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
divAmount   
**Schwab**   
number($double) example: 0.88 

**Logo Developer Portal**   
Dividend Amount 

string($yyyy-MM-dd'T'HH:mm:ssZ)   
Home   
~~divEx~~Date   
API Products User Guides 

divFreq 

divPayAmount divPayDate 

divYield 

eps   
example: 2021-05-07T00:00:00Z 

Dividend date in yyyy-mm-ddThh:mm:ssZ 

DivFreqinteger   
nullable: true 

Dividend frequency 1 – once a year or annually 2 – 2x a year or semi-annualy 3 \- 3x a year (ex. ARCO, EBRPF) 4 – 4x a year or quarterly 6 \- 6x per yr or every other month 11 – 11x a year (ex. FBND, FCOR) 12 – 12x a year or monthly 

Enum:   
\[ 1, 2, 3, 4, 6, 11, 12, \] 

number($double)   
example: 0.22 

Dividend Pay Amount 

string($date-time)   
example: 2021-05-13T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Dividend pay date in yyyy-mm-ddThh:mm:ssZ 

number($double)   
example: 0.7 

Dividend yield 

number($double)   
example: 4.45645 

Earnings per Share 

number($double)   
example: \-1   
fundLeverageFactor 

Fund Leverage Factor \+ \> 0 \<- 

FundStrategystring   
nullable: true 

fundStrategy 

nextDivExDate 

nextDivPayDate 

peRatio 

}   
FundStrategy "A" \- Active "L" \- Leveraged "P" \- Passive "Q" \- Quantitative "S" \- Short 

Enum:   
\[ A, L, P, Q, S, \] 

string($date-time)   
example: 2021-02-12T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Next Dividend date 

string($date-time)   
example: 2021-02-12T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Next Dividend pay date 

number($double)   
example: 28.599 

P/E Ratio 

FutureOptionResponse { 

description: Quote info of Future Option security 

AssetMainTypestring 

Instrument's asset type   
assetMainType 

Enum:   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] integer($int64) 

ssid   
example: 1234567890 SSID of instrument   
symbol string   
example: AAPL 

file:///Users/licaris/Downloads/market\_data\_api.html 64/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
Symbol of instrument 

boolean   
example: true   
**Logo Developer Portal** realtime 

Home   
is quote realtime 

~~quot~~e \#/components/schemas/QuoteFutureOptionQuoteFutureOption {   
API Products User Guides   
description: Quote data of Option security string 

askMICId askPrice askSize bidMICId bidPrice bidSize 

closePrice highPrice lastMICId   
example: XNYS 

ask MIC code 

number($double)   
example: 124.63 

Current Best Ask Price integer($int32)   
example: 700 

Number of shares for ask string   
example: XNYS 

bid MIC code 

number($double)   
example: 124.6 

Current Best Bid Price integer($int32)   
example: 300 

Number of shares for bid number($double)   
example: 126.27 

Previous day's closing price number($double)   
example: 126.99 

Day's high trade price string   
example: XNYS 

Last MIC Code 

lastPricenumber($double) example: 122.3   
integer($int32) 

lastSize 

lowPrice 

mark 

markChange netChange   
example: 100 

Number of shares traded with last trade number($double)   
example: 52.74 

Day's low trade price 

number($double)   
example: 52.93 

Mark price 

number($double)   
example: \-0.04 

Mark Price change 

number($double)   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756   
netPercentChange 

Net Percentage Change 

integer($int32) 

openInterest   
example: 317 Open Interest 

file:///Users/licaris/Downloads/market\_data\_api.html 65/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

openPrice   
number($double) example: 52.8 

**Logo Developer Portal** 

Price at market open integer($int64)   
Home 

API Products User Guides   
quoteTime 

securityStatus settlemetPrice tick 

tickAmount totalVolume 

tradeTime 

}   
example: 1621376892336 

Last quote time in milliseconds since Epoch 

string   
example: Normal 

Status of security 

number($double)   
example: 52.8 

Price at market open 

number($double)   
example: 0 

Tick Price 

number($double)   
example: 0 

Tick Amount 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

\#/components/schemas/ReferenceFutureOptionReferenceFutureOption { description: Reference data of Future Option security ContractTypestring 

contractType 

description exchange   
Indicates call or put 

Enum:   
\[ P, C \] 

string   
example: AMZN Aug 20 2021 2300 Put 

Description of Instrument 

string   
example: q 

Exchange Code 

string 

reference   
exchangeName 

Exchange Name 

number($double) 

multiplier 

expirationDate 

expirationStyle 

strikePrice 

underlying 

} 

}   
example: 100 

Option multiplier 

integer($int64) 

date of expiration in long 

string 

Style of expiration 

number($double)   
example: 2300 

Strike Price 

string   
example: AMZN Aug 20 2021 2300 Put A company, index or fund name 

file:///Users/licaris/Downloads/market\_data\_api.html 66/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal FutureResponse {   
Developer Portal   
**Charles**   
description: Quote info of Future security   
**Schwab**   
**Logo Developer Portal**   
AssetMainTypestring 

Instrument's asset type   
assetMainType   
Home 

API Products User Guides 

ssid 

symbol 

realtime   
Enum:   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] integer($int64)   
example: 1234567890 

SSID of instrument 

string   
example: AAPL 

Symbol of instrument 

boolean   
example: true 

is quote realtime 

quote \#/components/schemas/QuoteFutureQuoteFuture { description: Quote data of Future security string 

askMICId askPrice askSize askTime bidMICId bidPrice bidSize 

bidTime closePrice   
example: XNYS 

ask MIC code 

number($double)   
example: 4083.25 

Current Best Ask Price 

integer($int32)   
example: 36 

Number of shares for ask 

integer($int64)   
example: 1621376892336 

Last ask time in milliseconds since Epoch string   
example: XNYS 

bid MIC code 

number($double)   
example: 4083 

Current Best Bid Price 

integer($int32)   
example: 18 

Number of shares for bid 

integer($int64)   
example: 1621376892336 

Last bid time in milliseconds since Epoch number($double)   
example: 4123 

Previous day's closing price 

number($double)   
example: \-0.0756   
futurePercentChange 

Net Percentage Change 

number($double) 

highPrice lastMICId   
example: 4123 

Day's high trade price string   
example: XNYS 

Last MIC Code 

lastPricenumber($double)   
example: 4083 

file:///Users/licaris/Downloads/market\_data\_api.html 67/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

lastSize   
integer($int32) example: 7 

**Logo Developer Portal** 

Number of shares traded with last trade number($double)   
Home 

API Products User Guides   
lowPrice 

mark 

netChange 

openInterest 

openPrice 

quoteTime 

quotedInSession securityStatus settleTime 

tick 

tickAmount 

totalVolume 

tradeTime 

}   
example: 4075.5 

Day's low trade price 

number($double)   
example: 4083 

Mark price 

number($double)   
example: \-40 

Current Last-Prev Close 

integer($int32)   
example: 2517139 

Open interest 

number($double)   
example: 4114 

Price at market open 

integer($int64)   
example: 1621427004585 

Last quote time in milliseconds since Epoch 

boolean   
example: false 

quoted during trading session 

string   
example: Normal 

Status of security 

integer($int64)   
example: 1621376892336 

settlement time in milliseconds since Epoch 

number($double)   
example: 0.25 

Tick Price 

number($double)   
example: 12.5 

Tick Amount 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

reference \#/components/schemas/ReferenceFutureReferenceFuture { description: Reference data of Future security string 

description 

exchange 

exchangeName futureActiveSymbol   
example: E-mini S\&P 500 Index Futures,Jun-2021,ETH 

Description of Instrument 

string   
example: q 

Exchange Code 

string 

Exchange Name 

string   
example: /ESM21 

file:///Users/licaris/Downloads/market\_data\_api.html 68/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
futureExpirationDate   
Active symbol 

number($int64)   
example: 1623988800000   
**Logo Developer Portal** Home   
Future expiration date in milliseconds since epoch boolean   
API Products User Guides   
futureIsActive futureMultiplier futurePriceFormat   
example: true 

Future is active number($double) example: 50 

Future multiplier string   
example: D,D 

Price format 

number($double) example: 4123   
futureSettlementPrice 

Future Settlement Price 

string   
example: GLBX(de=1640;0=-1700151515301600;1=r-17001515r15301600d   
futureTradingHours 

product 

} 

} 

IndexResponse { 

15551640;7=d-16401555) 

Trading Hours 

string   
example: /ES 

Futures product symbol 

description: Quote info of Index security 

AssetMainTypestring 

Instrument's asset type   
assetMainType 

Enum:   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] integer($int64) 

ssid 

symbol realtime   
example: 1234567890 

SSID of instrument string   
example: AAPL 

Symbol of instrument boolean   
example: true 

is quote realtime 

quote \#/components/schemas/QuoteIndexQuoteIndex { description: Quote data of Index security number($double) 

52WeekHigh 52WeekLow closePrice highPrice   
example: 145.09 

Higest price traded in the past 12 months, or 52 weeks number($double)   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks number($double)   
example: 126.27 

Previous day's closing price 

number($double)   
example: 126.99 

Day's high trade price 

lastPricenumber($double)   
example: 122.3 

file:///Users/licaris/Downloads/market\_data\_api.html 69/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

lowPrice   
number($double) example: 52.74 

**Logo Developer Portal** 

Day's low trade price number($double)   
Home 

API Products User Guides   
netChange   
example: \-0.04 

Current Last-Prev Close number($double)   
example: \-0.0756   
netPercentChange 

Net Percentage Change 

number($double) 

openPrice 

securityStatus totalVolume 

tradeTime 

}   
example: 52.8 

Price at market open 

string   
example: Normal 

Status of security 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

\#/components/schemas/ReferenceIndexReferenceIndex { description: Reference data of Index security string 

reference   
description exchange   
example: DOW JONES 30 INDUSTRIALS 

Description of Instrument 

string   
example: q 

Exchange Code 

string   
exchangeName   
Exchange Name 

} 

} 

MutualFundResponse { 

description: Quote info of MutualFund security 

AssetMainTypestring 

Instrument's asset type   
assetMainType 

Enum:   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] MutualFundAssetSubTypestring   
nullable: true 

assetSubType 

ssid 

symbol 

realtime   
Asset Sub Type (only there if applicable) 

Enum:   
\[ OEF, CEF, MMF, \] 

integer($int64)   
example: 1234567890 

SSID of instrument 

string   
example: AAPL 

Symbol of instrument 

boolean   
example: true 

is quote realtime 

fundamental \#/components/schemas/FundamentalFundamental { 

description: Fundamentals of a security 

file:///Users/licaris/Downloads/market\_data\_api.html 70/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

avg10DaysVolume   
number($double) Average 10 day volume   
**Logo Developer Portal** avg1YearVolume 

Home 

API Products 

User Guides   
declarationDate 

divAmount 

divExDate 

divFreq 

divPayAmount 

divPayDate 

divYield 

eps   
number($double) 

Average 1 day volume 

string($date-time)   
example: 2021-04-28T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Declaration date in yyyy-mm-ddThh:mm:ssZ 

number($double)   
example: 0.88 

Dividend Amount 

string($yyyy-MM-dd'T'HH:mm:ssZ)   
example: 2021-05-07T00:00:00Z 

Dividend date in yyyy-mm-ddThh:mm:ssZ 

DivFreqinteger   
nullable: true 

Dividend frequency 1 – once a year or annually 2 – 2x a year or semi-annualy 3 \- 3x a year (ex. ARCO, EBRPF) 4 – 4x a year or quarterly 6 \- 6x per yr or every other month 11 – 11x a year (ex. FBND, FCOR) 12 – 12x a year or monthly 

Enum:   
\[ 1, 2, 3, 4, 6, 11, 12, \] 

number($double)   
example: 0.22 

Dividend Pay Amount 

string($date-time)   
example: 2021-05-13T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Dividend pay date in yyyy-mm-ddThh:mm:ssZ 

number($double)   
example: 0.7 

Dividend yield 

number($double)   
example: 4.45645 

Earnings per Share 

number($double)   
example: \-1   
fundLeverageFactor 

Fund Leverage Factor \+ \> 0 \<- 

FundStrategystring   
nullable: true 

fundStrategy 

nextDivExDate 

nextDivPayDate 

peRatio 

}   
FundStrategy "A" \- Active "L" \- Leveraged "P" \- Passive "Q" \- Quantitative "S" \- Short 

Enum:   
\[ A, L, P, Q, S, \] 

string($date-time)   
example: 2021-02-12T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Next Dividend date 

string($date-time)   
example: 2021-02-12T00:00:00Z   
pattern: yyyy-MM-dd'T'HH:mm:ssZ 

Next Dividend pay date 

number($double)   
example: 28.599 

P/E Ratio 

quote \#/components/schemas/QuoteMutualFundQuoteMutualFund { 

description: Quote data of Mutual Fund security 

file:///Users/licaris/Downloads/market\_data\_api.html 71/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

52WeekHigh   
number($double) example: 145.09 

**Logo Developer Portal**   
Higest price traded in the past 12 months, or 52 weeks number($double)   
Home 

API Products User Guides   
52WeekLow closePrice nAV 

netChange   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks number($double)   
example: 126.27 

Previous day's closing price 

number($double)   
example: 126.99 

Net Asset Value 

number($double)   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756   
netPercentChange 

Net Percentage Change 

string 

securityStatus totalVolume 

tradeTime 

}   
example: Normal 

Status of security 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

\#/components/schemas/ReferenceMutualFundReferenceMutualFund { description: Reference data of MutualFund security string 

reference   
cusip 

description exchange   
example: A23456789 

CUSIP of Instrument 

string   
example: Apple Inc. \- Common Stock 

Description of Instrument 

string   
default: m 

Exchange Code 

string   
default: MUTUAL\_FUND   
exchangeName 

Exchange Name 

} 

} 

OptionResponse { 

description: Quote info of Option security 

AssetMainTypestring 

Instrument's asset type   
assetMainType 

Enum:   
\[ BOND, EQUITY, FOREX, FUTURE, FUTURE\_OPTION, INDEX, MUTUAL\_FUND, OPTION \] integer($int64) 

ssid 

symbol   
example: 1234567890 

SSID of instrument string   
example: AAPL 

file:///Users/licaris/Downloads/market\_data\_api.html 72/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
realtime   
Symbol of instrument boolean   
example: true   
**Logo Developer Portal**   
is quote realtime   
Home   
quote \#/components/schemas/QuoteOptionQuoteOption { 

API Products User Guides   
description: Quote data of Option security number($double) 

52WeekHigh 52WeekLow askPrice 

askSize 

bidPrice 

bidSize 

closePrice delta 

gamma 

highPrice 

indAskPrice indBidPrice 

indQuoteTime impliedYield   
example: 145.09 

Higest price traded in the past 12 months, or 52 weeks 

number($double)   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks 

number($double)   
example: 124.63 

Current Best Ask Price 

integer($int32)   
example: 700 

Number of shares for ask 

number($double)   
example: 124.6 

Current Best Bid Price 

integer($int32)   
example: 300 

Number of shares for bid 

number($double)   
example: 126.27 

Previous day's closing price 

number($double)   
example: \-0.0407 

Delta Value 

number($double)   
example: 0.0001 

Gamma Value 

number($double)   
example: 126.99 

Day's high trade price 

number($double)   
example: 126.99 

Indicative Ask Price applicable only for Indicative Option Symbols number($double)   
example: 126.99 

Indicative Bid Price applicable only for Indicative Option Symbols integer($int64)   
example: 126.99 

Indicative Quote Time in milliseconds since Epoch applicable only for Indicative Option Symbols 

number($double)   
example: \-0.0067 

Implied Yield 

lastPricenumber($double) example: 122.3   
integer($int32) 

lastSize lowPrice   
example: 100 

Number of shares traded with last trade number($double)   
example: 52.74 

file:///Users/licaris/Downloads/market\_data\_api.html 73/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

mark   
Day's low trade price number($double) example: 52.93   
**Logo Developer Portal** Home 

Mark price 

number($double) 

API Products User Guides   
markChange 

markPercentChange moneyIntrinsicValue netChange 

netPercentChange openInterest 

openPrice 

quoteTime 

rho 

securityStatus   
example: \-0.01 

Mark Price change 

number($double)   
example: \-0.0189 

Mark Price percent change 

number($double)   
example: \-947.96 

Money Intrinsic Value 

number($double)   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756 

Net Percentage Change 

number($double)   
example: 317 

Open Interest 

number($double)   
example: 52.8 

Price at market open 

integer($int64)   
example: 1621376892336 

Last quote time in milliseconds since Epoch number($double)   
example: \-0.3732 

Rho Value 

string   
example: Normal 

Status of security 

number($double)   
example: 12.275   
theoreticalOptionValue 

Theoretical option Value 

number($double) 

theta 

timeValue 

totalVolume tradeTime 

underlyingPrice vega   
example: \-0.315 

Theta Value 

number($double)   
example: 12.22 

Time Value 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

number($double)   
example: 3247.96 

Underlying Price 

number($double)   
example: 1.4455 

Vega Value 

file:///Users/licaris/Downloads/market\_data\_api.html 74/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

volatility   
number($double) example: 0.0094 

**Logo Developer Portal** } 

Home 

Option Risk/Volatility Measurement 

reference \#/components/schemas/ReferenceOptionReferenceOption { 

API Products User Guides   
description: Reference data of Option security ContractTypestring 

contractType cusip   
Indicates call or put 

Enum:   
\[ P, C \] 

string   
example: 0AMZN.TK12300000 

CUSIP of Instrument 

integer($int32)   
example: 94   
daysToExpiration 

Days to Expiration 

string 

deliverables 

description 

exchange 

exchangeName exerciseType 

expirationDay expirationMonth 

expirationType 

expirationYear isPennyPilot   
example: $6024.37 cash in lieu of shares, 212 shares of AZN 

Unit of trade 

string   
example: AMZN Aug 20 2021 2300 Put 

Description of Instrument 

string   
default: o 

Exchange Code 

string 

Exchange Name 

ExerciseTypestring 

option contract exercise type America or European 

Enum:   
\[ A, E \] 

integer($int32)   
example: 20   
maximum: 31   
minimum: 1 

Expiration Day 

integer($int32)   
example: 8   
maximum: 12   
minimum: 1 

Expiration Month 

ExpirationTypestring 

M for End Of Month Expiration Calendar Cycle. (To match the last business day of the month), Q for Quarterly expirations (last business day of the quarter month MAR/JUN/SEP/DEC), W for Weekly expiration (also called Friday Short Term Expirations) and S for Expires 3rd Friday of the month (also known as regular options). 

Enum:   
\[ M, Q, S, W \] 

integer($int32)   
example: 2021 

Expiration Year 

boolean   
example: true 

Is this contract part of the Penny Pilot program 

lastTradingDay integer($int64)   
example: 1629504000000 

file:///Users/licaris/Downloads/market\_data\_api.html 75/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab** 

multiplier   
milliseconds since epoch number($double)   
example: 100   
**Logo Developer Portal** 

Home 

API Products   
Option multiplier 

SettlementTypestring 

option contract settlement type AM or PM   
User Guides 

} 

QuoteEquity {   
settlementType 

strikePrice 

underlying 

} 

Enum:   
\[ A, P \] 

number($double)   
example: 2300 

Strike Price 

string   
example: AMZN Aug 20 2021 2300 Put A company, index or fund name 

description: Quote data of Equity security number($double) 

52WeekHigh 52WeekLow askMICId 

askPrice 

askSize 

askTime 

bidMICId 

bidPrice 

bidSize 

bidTime 

closePrice highPrice   
example: 145.09 

Higest price traded in the past 12 months, or 52 weeks number($double)   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks string   
example: XNYS 

ask MIC code 

number($double)   
example: 124.63 

Current Best Ask Price 

integer($int32)   
example: 700 

Number of shares for ask 

integer($int64)   
example: 1621376892336 

Last ask time in milliseconds since Epoch string   
example: XNYS 

bid MIC code 

number($double)   
example: 124.6 

Current Best Bid Price 

integer($int32)   
example: 300 

Number of shares for bid 

integer($int64)   
example: 1621376892336 

Last bid time in milliseconds since Epoch 

number($double)   
example: 126.27 

Previous day's closing price 

number($double)   
example: 126.99 

Day's high trade price 

lastMICId string   
example: XNYS 

file:///Users/licaris/Downloads/market\_data\_api.html 76/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
Last MIC Code 

lastPricenumber($double)   
**Schwab** 

example: 122.3   
**Logo Developer Portal** integer($int32)   
lastSize   
Home 

API Products User Guides ~~lowPrice~~ 

mark 

markChange   
example: 100 

Number of shares traded with last trade number($double) 

Day's low trade price 

number($double)   
example: 52.93 

Mark price 

number($double)   
example: \-0.01 

Mark Price change 

number($double)   
example: \-0.0189   
markPercentChange 

Mark Price percent change 

number($double) 

netChange 

netPercentChange openPrice 

quoteTime 

securityStatus totalVolume 

tradeTime 

volatility 

} 

QuoteForex {   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756 

Net Percentage Change 

number($double)   
example: 52.8 

Price at market open 

integer($int64)   
example: 1621376892336 

Last quote time in milliseconds since Epoch 

string   
example: Normal 

Status of security 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

number($double)   
example: 0.0094 

Option Risk/Volatility Measurement 

description: Quote data of Forex security number($double) 

52WeekHigh 52WeekLow askPrice 

askSize   
example: 145.09 

Higest price traded in the past 12 months, or 52 weeks number($double)   
example: 77.581 

Lowest price traded in the past 12 months, or 52 weeks number($double)   
example: 124.63 

Current Best Ask Price 

integer($int32)   
example: 700 

file:///Users/licaris/Downloads/market\_data\_api.html 77/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
bidPrice   
Number of shares for ask number($double)   
example: 124.6   
**Logo Developer Portal**   
Current Best Bid Price   
Home 

API Products   
bidSize   
User Guides closePrice 

highPrice   
integer($int32)   
example: 300 

Number of shares for bid number($double)   
example: 126.27 

Previous day's closing price number($double)   
example: 126.99 

Day's high trade price 

lastPricenumber($double) example: 122.3   
integer($int32) 

lastSize 

lowPrice mark 

netChange   
example: 100 

Number of shares traded with last trade number($double)   
example: 52.74 

Day's low trade price 

number($double)   
example: 52.93 

Mark price 

number($double)   
example: \-0.04 

Current Last-Prev Close 

number($double)   
example: \-0.0756   
netPercentChange 

Net Percentage Change 

number($double) 

openPrice 

quoteTime 

securityStatus tick 

tickAmount totalVolume 

tradeTime 

} 

QuoteFuture {   
example: 52.8 

Price at market open 

integer($int64)   
example: 1621376892336 

Last quote time in milliseconds since Epoch 

string   
example: Normal 

Status of security 

number($double)   
example: 0 

Tick Price 

number($double)   
example: 0 

Tick Amount 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

description: Quote data of Future security 

askMICId string   
example: XNYS 

file:///Users/licaris/Downloads/market\_data\_api.html 78/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
askPrice   
ask MIC code number($double) example: 4083.25   
**Logo Developer Portal**   
Current Best Ask Price   
Home 

API Products   
askSize   
User Guides askTime 

bidMICId 

bidPrice 

bidSize 

bidTime 

closePrice   
integer($int32)   
example: 36 

Number of shares for ask 

integer($int64)   
example: 1621376892336 

Last ask time in milliseconds since Epoch string   
example: XNYS 

bid MIC code 

number($double)   
example: 4083 

Current Best Bid Price 

integer($int32)   
example: 18 

Number of shares for bid 

integer($int64)   
example: 1621376892336 

Last bid time in milliseconds since Epoch number($double)   
example: 4123 

Previous day's closing price 

number($double)   
example: \-0.0756   
futurePercentChange 

Net Percentage Change 

number($double) 

highPrice lastMICId   
example: 4123 

Day's high trade price string   
example: XNYS 

Last MIC Code 

lastPricenumber($double) example: 4083   
integer($int32) 

lastSize 

lowPrice 

mark 

netChange openInterest openPrice   
example: 7 

Number of shares traded with last trade number($double)   
example: 4075.5 

Day's low trade price 

number($double)   
example: 4083 

Mark price 

number($double)   
example: \-40 

Current Last-Prev Close 

integer($int32)   
example: 2517139 

Open interest 

number($double)   
example: 4114 

Price at market open 

quoteTime integer($int64)   
example: 1621427004585 

file:///Users/licaris/Downloads/market\_data\_api.html 79/134  
2/26/26, 6:51 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
quotedInSession   
Last quote time in milliseconds since Epoch boolean   
example: false   
**Logo Developer Portal**   
quoted during trading session   
Home 

API Products   
securityStatus User Guides 

settleTime 

tick 

tickAmount 

totalVolume 

tradeTime 

} 

QuoteFutureOption {   
string   
example: Normal 

Status of security 

integer($int64)   
example: 1621376892336 

settlement time in milliseconds since Epoch 

number($double)   
example: 0.25 

Tick Price 

number($double)   
example: 12.5 

Tick Amount 

integer($int64)   
example: 20171188 

Aggregated shares traded throughout the day, including pre/post market hours. integer($int64)   
example: 1621376731304 

Last trade time in milliseconds since Epoch 

description: Quote data of Option security string 

askMICId askPrice askSize bidMICId bidPrice bidSize 

closePrice highPrice lastMICId   
example: XNYS 

ask MIC code 

number($double)   
example: 124.63 

Current Best Ask Price integer($int32)   
example: 700 

Number of shares for ask string   
example: XNYS 

bid MIC code 

number($double)   
example: 124.6 

Current Best Bid Price integer($int32)   
example: 300 

Number of shares for bid number($double)   
example: 126.27 

Previous day's closing price number($double)   
example: 126.99 

Day's high trade price string   
example: XNYS 

Last MIC Code 

lastPricenumber($double)   
example: 122.3 

lastSize integer($int32)   
example: 100 

file:///Users/licaris/Downloads/market\_data\_api.html 80/134