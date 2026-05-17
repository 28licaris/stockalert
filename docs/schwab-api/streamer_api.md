2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal **Developer Portal** 

API Products Trader API \- Individual Market Data Production 

**Market Data Production** 

Specifications 

Documentation 

Schwab Streamer API 

The Streamer API enables clients to connect into different services to stream market data and account activity with JSON-formatting via WebSockets. Authentication and entitlements are provided via the Access token generated from the POST Token endpoint. Streamer information to establish the connection can be found on the GET User Preference endpoint. Client as referenced throughout this document is in reference to the application. 

Contents 

1\. API Contract 

1\. Services available: 

| Service Name  | Description  | Delivery Type |
| :---- | :---- | :---- |
| LEVELONE\_EQUITIES  | Level 1 Equities  | Change |
| LEVELONE\_OPTIONS  | Level 1 Options  | Change |
| LEVELONE\_FUTURES  | Level 1 Futures  | Change |
| LEVELONE\_FUTURES\_OPTIONS  | Level 1 Futures Options  | Change |
| LEVELONE\_FOREX  | Level 1 Forex  | Change |
| NYSE\_BOOK  | Level Two book for Equities  | Whole |
| NASDAQ\_BOOK  | Level Two book for Equities  | Whole |
| OPTIONS\_BOOK  | Level Two book for Options  | Whole |
| CHART\_EQUITY  | Chart candle for Equities  | All Sequence |
| CHART\_FUTURES  | Chart candle for Futures  | All Sequence |
| SCREENER\_EQUITY  | Advances and Decliners for Equities  | Whole |
| SCREENER\_OPTION  | Advances and Decliners for Options  | Whole |
| ACCT\_ACTIVITY  | Get account activity information such as order fills, etc  | All Sequence |

2\. Request Format 

A client request will consist of an array of one or more commands. Each command will include: 

| Request  | Name  | Parameter |
| :---- | :---- | :---- |
| service  | Service Name (required) | ADMIN, LEVELONE\_EQUITY etc. Please see Service Names table above. |
| command  | Command  (required) | LOGIN, SUBS, ADD, UNSUBS, VIEW, LOGOUT |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 1/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Request Developer Portal | Name  | Parameter |
| ----- | :---- | :---- |
| requestid  | Request ID  (required) | Unique number that will identify this request. |
| SchwabClientCustomerId Client's | customer ID | \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  | Client's sessionID | \`schwabClientCorrelId\` as found in GET User Preference endpoint. Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | Any parameter (optional) | fields, version, credential, symbol, frequency, period, etc |

| Command Name |  |
| ----- | :---- |
| LOGIN  | Initial request when opening a new connection. This must be successful before sending other commands. |
| SUBS  | Subscribes to a set of symbols or keys for a particular service. This overwrites all previously subscribed symbols for that service. This is a convenient way to wipe out old subscription list and start fresh, but it's not the most efficient. If you only want to add one symbol to 300 already subscribed, use an ADD instead.  For example:  1\. SUBS A,B,C (fresh sub for LEVELONE\_EQUITIES)  2\. SUBS A (fresh sub for LEVELONE\_EQUITIES, previous SUBS of B,C are unsub'ed, only A is sub'ed) |
| ADD  | Adds a new symbol for a particular service. This does NOT wipe out previous symbols that were already subscribed. It is OK to use ADD for first subscription command instead of SUBS.  For example:  1\. ADD A,B (fresh sub for LEVELONE\_EQUITIES)  2\. ADD C (additional symbol C added to A, B. All 3 symbols will stream) |
| UNSUBS  | This unsubscribes a symbol to a list of subscribed symbol for a particular service. |
| VIEW  | This changes the field subscription for a particular service. It will apply to all symbols for that particular service. |
| LOGOUT  | Logs out of the streamer connection. Streamer will close the connection. |

Example: 

One Request 

{ 

"requestid": "0", 

"service": "LEVELONE\_EQUITIES", 

"command": "SUBS", 

"SchwabClientCustomerId": "Someone", 

"SchwabClientCorrelId": "3be0b7e7-5b8b-4fd3-9bed-7f49106cfe1", 

"parameters": { 

"keys": "AAPL", 

"fields": 

"0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51 } 

} 

Multiple Requests 

{ 

"requests": \[ 

{ 

"requestid": "1", 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 2/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

"service": "ADMIN", 

**Developer Portal**   
"command": "LOGIN", 

"SchwabClientCustomerId": "Someone", 

"SchwabClientCorrelId": "2be0b7e7-5b8b-4fd3-9bed-7f49106cfe1", 

"parameters": { 

"Authorization": "PN", 

"SchwabClientChannel": "IO", 

"SchwabClientFunctionId": "Tradeticket" 

} 

}, 

{ 

"requestid":"3", 

"service":"LEVELONE\_EQUITIES", 

"command":"SUBS", 

"SchwabClientCustomerId":"Someone", 

"SchwabClientCorrelId":"2be0b7e7-5b8b-4fd3-9bed-7f49106cfe1", 

"parameters":{ 

"keys":"AAPL", 

"fields":"0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19" 

} 

} 

\] 

} 

3\. Response Format 

There are currently three types of responses: 

Response â€“ Response to a request 

Notify â€“ Notification of heartbeats 

Data â€“ Streaming market data 

A client response will consist of an array of one or more responses. Each response will include: 

| Response Type  | Request  | Name  | Parameter |
| :---- | ----- | ----- | :---- |
| response notify data | service  | Service Name  | ADMIN, LEVELONE\_EQUITY, etc. Please see Service Names table in section 5\. |
|  |  | requestid Request ID  | Unique number that will identify the original request |
|  |  | command Command from the request | LOGIN, SUBS, ADD, UNSUBS, VIEW, LOGOUT |
|  | content  | Data content |  |

Examples: 

{"notify":\[{"heartbeat":"1668715930582"}\]} 

{ 

"response": \[ 

{ 

"service": "LEVELONE\_EQUITIES", 

"command": "SUBS", 

"requestid": "0", 

"SchwabClientCorrelId": "3be0b7e7-5b8b-4fd3-9bed-7f49106cfe1", 

"timestamp": 1668715930582, 

"content": { 

"code": 0, 

"msg": "SUBS command succeeded" 

} 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 3/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal }   
\] 

} 

{ 

"data": \[ {   
**Developer Portal** 

"service": "LEVELONE\_EQUITIES", 

"timestamp": 1668715930585, 

"command": "SUBS", 

"content": \[ 

{ 

"1": 149.81, 

"2": 149.82, 

"3": 149.811, 

"4": 4, 

"5": 2, 

"6": "Q", 

"7": "P", 

"8": 56049058, 

"9": 300, 

"10": 151.48, 

"11": 146.15, 

"12": " ", 

"13": 142.41, 

"14": "Q", 

"15": false, 

"16": "APPLE INC", 

"17": "D", 

"18": 146.43, 

"19": 7.401, 

"20": 182.94, 

"21": 129.04, 

"22": 0.04062, 

"23": 0, 

"24": 0, 

"25": 0, 

"26": "NASDAQ", 

"27": "", 

"28": true, 

"29": true, 

"30": 149.811, 

"31": 300, 

"32": 7.401, 

"33": "Normal", 

"34": 149.811, 

"35": 1668715930570, 

"36": 1668715930345, 

"37": 1668715930345, 

"38": 1668715930570, 

"39": 1668715930522, 

"40": "XNAS", 

"41": "ARCX", 

"42": "XADF", 

"43": 5.19696651, 

"44": 5.19696651, 

"45": 7.401, 

"46": 5.19696651, 

"key": "AAPL", 

"delayed": false 

} 

\] 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 4/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal }   
\] 

}   
**Developer Portal** 

4\. Response Codes 

|  | Code Name  | Description | Connection Severed  | Error Notes |
| :---- | :---- | :---- | :---- | :---- |
| 0  | SUCCESS  | The request was successful | No  | n/a \- success |
| 3  | LOGIN\_DENIED  | The user login has been denied | Yes  | Client should reconnect and re-login with new token. Client to determine if failed logins are expected. |
| 9  | UNKNOWN\_FAILURE  | Error of last-resort when no specific error was caught | TBD  | Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the \`schwabClientCorrelId\` of subscription. |
| 11  | SERVICE\_NOT\_AVAILABLE  | The service is not available | No  | Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the \`schwabClientCorrelId\` of subscription. Either client is requesting an unsupported service or the service is not running from the source. |
| 12  | CLOSE\_CONNECTION  | You've reached the maximum number of connections  allowed. | Yes  | Client to determine if max connections are expected and proper response to customer. A limit of 1 Streamer connection at any given time from a given user is available. |
| 19  | REACHED\_SYMBOL\_LIMIT  | Subscribe or Add command has  reached a total  subscription symbol limit | No  | Client to determine if symbol limit is expected and proper response to customer. |
| 20  | STREAM\_CONN\_NOT\_FOUND  | No connection  found for user or new session but no login request | TBD  | Server cannot find the connection based on the provided SchwabClientCustomerId &  SchwabClientCorrelId in the request.Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the \`schwabClientCorrelId\` of subscription.  Common causes:  Client does not wait for a successful LOGIN response and issues a command immediately after the LOGIN command. There could be a race condition where the SUB is processed before the LOGIN.  Client modifies SchwabClientCustomerId or SchwabClientCorrelId after logging in.  Streamer has disconnected the client while processing the command. |
| 21  | BAD\_COMMAND\_FORMAT  | Command fails to match specification | No  | Client should investigate why a command is not formatted properly |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 5/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Code Name Developer Portal | Description | Connection Severed  | Error Notes |
| :---- | ----- | :---- | :---- | :---- |
| 22  | FAILED\_COMMAND\_SUBS  | Subscribe  command could not be completed  successfully | No  | Should be investigated by Trader API team. Please contact TraderAPI@Schwab.com if you see this with the \`schwabClientCorrelId\` of subscription. Common causes:  Two or more commands are processed in parallel causing one to fail. |
| 23  | FAILED\_COMMAND\_UNSUBS  | Unsubscribe  command could not be completed  successfully |  |  |
| 24  | FAILED\_COMMAND\_ADD  | Add command  could not be  completed  successfully |  |  |
| 25  | FAILED\_COMMAND\_VIEW  | View command  could not be  completed  successfully |  |  |
| 26  | SUCCEEDED\_COMMAND\_SUBS  | Subscribe  command  completed  successfully | No  | n/a \- success |
| 27  | SUCCEEDED\_COMMAND\_UNSUBS Unsubscribe | command  completed  successfully |  |  |
| 28  | SUCCEEDED\_COMMAND\_ADD  | Add command  completed  successfully |  |  |
| 29  | SUCCEEDED\_COMMAND\_VIEW  | View command  completed  successfully |  |  |
| 30  | STOP\_STREAMING  | Signal that  streaming has been terminated due to administrator  action, inactivity, or slowness | Yes  | See message provided for details.  Common Causes:  Typically due to no subscriptions. |

5\. Delivery Types 

| Delivery  Types  | Description |
| ----- | :---- |
| All Sequence  | All data is streamed to the client and includes a sequence number. Data is not conflated by the streamer although the underlying source of the data may conflate. |
| Change  | Only fields that clients are interested in, and have changed, are streamed to the client. Data is conflated by the streamer. |
| Whole  | Data is streamed as a whole unit to the client, in throttled mode. |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 6/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Delivery  Types  | Developer Portal Description  |
| ----- | :---- |
| All Sequence  | All data is streamed to the client and includes a sequence number. Data is not conflated by the streamer although the underlying source of the data may conflate. |

2\. Admin Services 

1\. Login Request 

| Delivery Types  | Description  | Type  | Length  | Description |
| ----- | ----- | ----- | ----- | ----- |
| service  |  | String  | Variable ADMIN |  |
| command  |  | String  | Variable LOGIN |  |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | Authorization  | String  |  | Variable Access token as found from POST Token endpoint. |
|  | SchwabClientChannel  | String  | 2  | Identifies the channel as found through the GET User Preferences endpoint. |
|  | SchwabClientFunctionId String  |  | 5  | Identifies the page or source in the channel where quote is being called from (5 alphanumeric).  Found through the GET User Preferences endpoint. |

Streamer LOGIN Request Example: 

{ 

"requests": \[ 

{ 

"requestid": "1", 

"service": "ADMIN", 

"command": "LOGIN", 

"SchwabClientCustomerId": "Someone", 

"SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe96", 

"parameters": { 

"Authorization": "Access Token", 

"SchwabClientChannel": "N9", 

"SchwabClientFunctionId": "APIAPP" 

} 

} 

\] 

} 

2\. Login Response 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 7/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Type  | Request Developer Portal | Name  | Type  | Description |
| ----- | :---- | ----- | ----- | :---- |
| response service  |  | ADMIN |  |  |
|  | requestid  | Unique request ID number |  |  |
|  | command  | LOGIN |  |  |
|  |  | SchwabClientCorrelId Correlation ID string passed by client |  |  |
|  | timestamp  | Milliseconds since epoch |  |  |
|  | content  | code  |  | Integer 0 \= Success, 3 \= Login denied |
|  |  | msg  | String  | server=hostname-instance (for troubleshooting purposes)  status=PN (Non-Paying Pro)  NP (Non-Pro)  PP (Paying-Pro)  if no entitlements, client will get nfl/delayed quotes error message if there's a login issue |

Streamer LOGIN Response Examples: 

Login Successful 

{ 

"response": \[ 

{ 

"service": "ADMIN", 

"command": "LOGIN", 

"requestid": "1", 

"SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe96", 

"timestamp": 1669828276886, 

"content": { 

"code": 0, 

"msg": "server=s0166bdv-1;status=PN" 

} 

} 

\] 

} 

Login Denied 

{ 

"response": \[ 

{ 

"service": "ADMIN", 

"command": "LOGIN", 

"requestid": "1", 

"SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe96", 

"timestamp": 1669828982588, 

"content": { 

"code": 3, 

"msg": "Login Denied.: token is invalid or has expired." 

} 

} 

\] 

} 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 8/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

3\. Logout request 

**Developer Portal** 

| Streamer Contract  name  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- |
| service  | String  | Variable ADMIN |  |
| command  | String  |  | Variable LOGOUT |
| requestid  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId String  |  |  | Variable Identifies the page or source in the channel where quote is being called from (5 alphanumeric).  Found through the GET User Preferences endpoint. |
| SchwabClientCorrelId  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | String  |  | Variable Can leave empty |

4\. Logout response 

| Type  | Request  | Name  | Type  | Description |
| ----- | :---- | :---- | ----- | :---- |
| response  | service  | ADMIN |  |  |
|  | requestid  | Unique request ID number |  |  |
|  | command  | LOGIN |  |  |
|  | SchwabClientCorrelId  | Correlation ID string passed by client |  |  |
|  | timestamp  | Milliseconds since epoch |  |  |
|  | content  | code  | Integer  | 0 \= Success, 3 \= Login denied |
|  |  | msg  | String  | SUCCESS, FAILURE |

Streamer Logout Response Examples: 

{ 

"response": \[ 

{ 

"service": "ADMIN", 

"command": "LOGOUT", 

"requestid": "0", 

"SchwabClientCorrelId": "5be0b7e7-5b8b-4fd3-9bed-7f49106cfe95", 

"timestamp": 1669830137089, 

"content": { 

"code": 0, 

"msg": "SUCCESS" 

} 

} 

\] 

} 

3\. LEVELONE Services 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 9/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

1\. LEVELONE\_EQUITIES 

**Developer Portal** 

Level One Equities Request 

| Streamer Contract  name  |  |  | Type Length Description |  |
| ----- | ----- | ----- | :---- | :---- |
| service  |  | String  | Variable  | LEVELONE\_EQUITIES |
| command  |  | String  | Variable  | SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  | Integer Variable  | Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  | Variable  | \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  | Variable  | Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | keys  | String  | Variable  | Schwab-standard symbols in uppercase and separated by commas e.g. AAPL,TSLA,IBM |
|  |  | fields String  | Variable  | Please see the LEVELONE\_EQUITIES Field Definition table below |

LEVELONE\_EQUITIES Request Example: 

{ 

"requests": \[ 

{ 

"service": "LEVELONE\_EQUITIES", 

"requestid": 1, 

"command": "SUBS", 

"SchwabClientCustomerId": "Someone", 

"SchwabClientCorrelId": "29bdf6d-b9d0-46dd-8786-424e1577bd", 

"parameters": { 

"keys": "SCHW,AAPL,SPY", 

"fields": "0,1,2,3,4,5,8,10 " 

} 

} 

\] 

} 

Response Field Definitions 

Outside of fields that can be subscribed to, Streamer also returns initial data that indicates whether the data is real time or NFL (delayed). 

| Field Name  | Type | Field  Description  | Notes, Examples Source |
| :---- | ----- | ----- | :---- |
| key  | String  | Usually this is the symbol | AAPL |
| delayed  |  | boolean Whether data is from the SIP or NFL | \- false : data is from a SIP  SIP stands for Securities Information Processor. Often considered the example for market data around the world, a SIP will collect trade and quote data from multiple exchanges and consolidate these sources into a single source of information. \- true : data is from an NFL source  NFL stands for Non-Fee Liable. This either means the result is returning delayed data (typically options, futures and futures options) or the result is returning real-time data from a subset of exchanges and therefore does not contain all markets in the National  Plan (typically equity data). Delayed quotes do not represent the most recent last or bid/ask; real-time quotes from the subset of exchanges may not contain the most recent last or bid/ask. |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 10/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Field Name  | Developer PortalType  | Field  Description  | Notes, Examples Source |
| ----- | ----- | ----- | :---- |
| assetMainType String  |  | Asset Type  | BOND, EQUITY, ETF, EXTENDED, FOREX, FUTURE, FUTURE\_OPTION, FUNDAMENTAL, INDEX, INDICATOR, MUTUAL\_FUND, OPTION, UNKNOWN |
| assetSubType  | String  | Asset sub type  | ADR, CEF, COE, ETF, ETN, GDR, OEF, PRF, RGT, UIT, WAR |
| cusip  | String  | 9 digits CUSIP  | CUSIP number for the instrument, such as 594918104 |

LEVELONE\_EQUITIES Response Example: 

{ 

"data": \[ 

{ 

"service": "LEVELONE\_EQUITIES", 

"timestamp": 1714949592301, 

"command": "SUBS", 

"content": \[ 

{ 

"key": "SCHW", 

"delayed": false, 

"assetMainType": "EQUITY", 

"assetSubType": "COE", 

"cusip": "808513105", 

"1": 76.08, 

"2": 76.49, 

"3": 76.44, 

"4": 3, 

"5": 1, 

"8": 5414735, 

"10": 76.47 

}, 

{ 

"key": "AAPL", 

"delayed": false, 

"assetMainType": "EQUITY", 

"assetSubType": "COE", 

"cusip": "037833100", 

"1": 183.75, 

"2": 183.8, 

"3": 183.8, 

"4": 1, 

"5": 2, 

"8": 163224109, 

"10": 187 

}, 

{ 

"key": "SPY", 

"delayed": false, 

"assetMainType": "EQUITY", 

"assetSubType": "ETF", 

"cusip": "78462F103", 

"1": 512.3, 

"2": 512.32, 

"3": 511.29, 

"4": 8, 

"5": 1, 

"8": 72756709, 

"10": 512.55 

} 

\] 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 11/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal }   
\] 

}   
**Developer Portal** 

|  | Fields Field Name  | Type  | Field Description  | Notes, Examples Source |
| :---- | ----- | ----- | :---- | :---- |
| 0  | Symbol  | String  | Ticker symbol in upper case. |  |
| 1  | Bid Price  | double  | Current Bid Price |  |
| 2  | Ask Price  | double  | Current Ask Price |  |
| 3  | Last Price  | double  | Price at which the last trade was matched |  |
| 4  | Bid Size  | int  | Number of shares for bid  | Units are "lots" (typically 100 shares per lot)Note for NFL data this field can be 0 with a non-zero bid price which representing a bid size of less than 100 shares. |
| 5  | Ask Size  | int  | Number of shares for ask  | See bid size notes. |
| 6  | Ask ID  | char  | Exchange with the ask |  |
| 7  | Bid ID  | char  | Exchange with the bid |  |
| 8  | Total Volume  | long  | Aggregated shares traded throughout the day, including pre/post market hours. | Volume is set to zero at 7:28am ET. |
| 9  | Last Size  | long  | Number of shares traded with last trade | Units are shares |
| 10  | High Price  | double  | Day's high trade price  | According to industry standard, only regular session trades set the High and Low  If a stock does not trade in the regular session, high and low will be zero.  High/low reset to ZERO at 3:30am ET |
| 11  | Low Price  | double  | Day's low trade price  | See High Price notes |
| 12  | Close Price  | double  | Previous day's closing price  | Closing prices are updated from the DB at 3:30 AM ET. |
| 13  | Exchange ID  | char  | Primary "listing" Exchange  | As long as the symbol is valid, this data is always present This field is updated every time the closing prices are loaded from DB  Exchange CodeRealtime/NFL  AMEX A Both  Indicator : Realtime Only  Indices 0 Realtime Only  Mutual Fund3 Realtime Only  NASDAQ Q Both  NYSE N Both  Pacific P Both  Pinks 9 Realtime Only  OTCBB U Realtime Only |
| 14  | Marginable  |  | boolean Stock approved by the Federal Reserve and an investor's broker as being eligible for providing collateral for margin debt. |  |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 12/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Fields Field Name Developer Portal | Type  | Field Description  | Notes, Examples Source |
| :---- | ----- | ----- | ----- | :---- |
| 15  | Description  | String  | A company, index or fund name | Once per day descriptions are loaded from the database at 7:29:50 AM ET. |
| 16  | Last ID  | char  | Exchange where last trade was executed |  |
| 17  | Open Price  | double  | Day's Open Price According toindustry standard, only  regular session trades set the open.  If a stock does not trade during the regular session, then the open price is 0\. In the pre-market session, open is blank because pre market session trades do not set the open.  Open is set to ZERO at  3:30am ET. |  |
| 18  | Net Change  | double  |  | LastPrice \- ClosePrice  If close is zero, change will be zero |
| 19  | 52 Week High  | double  | Higest price traded in the past 12 months, or 52 weeks | Calculated by merging intraday high (from fh) and 52-week high (from db) |
| 20  | 52 Week Low  | double  | Lowest price traded in the past 12 months, or 52 weeks | Calculated by merging intraday low (from fh) and 52-week low (from db) |
| 21  | PE Ratio  | double  | Price-to-earnings ratio.  The P/E equals the price of a share of stock, divided by the company's earnings-per share. | Note that the "price of a share of stock" in the definition does update during the day so this field has the potential to stream. However, the current implementation uses the closing price and therefore does not stream throughout the day. |
| 22  | Annual  Dividend  Amount | double  | Annual Dividend Amount |  |
| 23  | Dividend Yield  | double  | Dividend Yield |  |
| 24  | NAV  | double  | Mutual Fund Net Asset Value  | Load various times after market close |
| 25  | Exchange  Name | String  | Display name of exchange |  |
| 26  | Dividend Date  | String |  |  |
| 27  | Regular Market Quote | boolean  |  | Is last quote a regular quote |
| 28  | Regular Market Trade | boolean  |  | Is last trade a regular trade |
| 29  | Regular Market Last Price | double  |  | Only records regular trade |
| 30  | Regular Market Last Size | integer  |  | Currently realize/100, only records regular trade |
| 31  | Regular Market Net Change | double  |  | RegularMarketLastPrice \- ClosePrice |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 13/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Fields Field Name Developer Portal | Type  | Field Description  | Notes, Examples Source |
| :---- | ----- | ----- | ----- | :---- |
| 32  | Security Status String  |  |  | Indicates a symbols current trading status, Normal, Halted, Closed |
| 33  | Mark Price  | double  | Mark Price |  |
| 34  | Quote Time in Long | Long  | Last time a bid or ask updated in milliseconds since Epoch | The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC. |
| 35  | Trade Time in Long | Long  | Last trade time in milliseconds since Epoch | The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC. |
| 36  | Regular Market Trade Time in Long | Long  | Regular market trade time in milliseconds since Epoch | The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC. |
| 37  | Bid Time  | long  | Last bid time in milliseconds since Epoch | The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC. |
| 38  | Ask Time  | long  | Last ask time in milliseconds since Epoch | The difference, measured in milliseconds, between the time an event occurs and midnight, January 1, 1970 UTC. |
| 39  | Ask MIC ID  | String  | 4-chars Market Identifier Code |  |
| 40  | Bid MIC ID  | String  | 4-chars Market Identifier Code |  |
| 41  | Last MIC ID  | String  | 4-chars Market Identifier Code |  |
| 42  | Net Percent  Change | double  | Net Percentage Change  | NetChange / ClosePrice \* 100 |
| 43  | Regular Market Percent  Change | double  | Regular market hours  percentage change | RegularMarketNetChange / ClosePrice \* 100 |
| 44  | Mark Price Net Change | double  | Mark price net change  | 7.97 |
| 45  | Mark Price  Percent  Change | double  | Mark price percentage change 4.2358 |  |
| 46  | Hard to BorrowQuantity | integer  |  | \-1 \= NULL  \>= 0 is valid quantity |
| 47  | Hard To  Borrow Rate | double  |  | null \= NULL  valid range \= \-99,999.999 to \+99,999.999 |
| 48  | Hard to Borrow integer  |  |  | \-1 \= NULL  1 \= true  0 \= false |
| 49  | shortable  | integer  |  | \-1 \= NULL  1 \= true  0 \= false |
| 50  | Post-Market  Net Change | double  | Change in price since the end of the regular session  (typically 4:00pm) | PostMarketLastPrice \- RegularMarketLastPrice |

51 Post-Market   
double Percent Change in price since   
PostMarketNetChange / RegularMarketLastPrice \* 100 

Percent   
the end of the regular session 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 14/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Fields Field Name Developer Portal | Type  | Field Description  | Notes, Examples Source |
| :---- | :---- | ----- | :---- | :---- |
|  | Change  |  | (typically 4:00pm) |  |

2\. LEVELONE\_OPTIONS 

Please refer to LEVELONE\_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE\_EQUITIES with LEVELONE\_OPTIONS. Level One Options Request 

| Streamer Contract  name  |  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- | ----- |
| service  |  | String  |  | Variable LEVELONE\_OPTIONS |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | keys  | String  |  | Variable Options symbols in uppercase and separated by commas Schwab-standard option symbol format:  RRRRRRYYMMDDsWWWWWddd  Where:  R is the space-filled root  symbol YY is the expiration year  MM is the expiration month  DD is the expiration day  s is the side: C/P (call/put)  WWWWW is the whole portion of the strike price  nnn is the decimal portion of the strike price  e.g.: AAPL 251219C00200000 |
|  |  | fields String  |  | Variable Please see the LEVELONE\_OPTIONS Field Definition table below |

Response Field Definitions 

| Streamer Contract name  |  | Type  | Length  | Description |  |  |
| :---- | ----- | :---: | :---- | ----- | :---- | :---- |
| 0  | Symbol  | String  | Ticker symbol in upper case.  | N/A  | N/A |  |
| 1  | Description  | String  | A company, index or fund name  | Yes  | Yes  | Descriptions are loaded from the database daily at 3:30 am ET. |
| 2  | Bid Price  | double  | Current Bid Price  | Yes  | No |  |
| 3  | Ask Price  | double  | Current Ask Price  | Yes  | No |  |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 15/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name  | Developer Portal | Type  | Length  | Description |  |  |
| :---- | ----- | ----- | :---- | ----- | :---- | :---- |
| 4  | Last Price  | double  | Price at which the last trade was matched | Yes  | No |  |
| 5  | High Price  | double  | Day's high trade price  | Yes  | No  | According to  industry standard, only regular sessiontrades set the High and Low.  If a stock does not trade in the regular session, high and low will be zero.  High/low reset to zero at 3:30am ET |
| 6  | Low Price  | double  | Day's low trade price  | Yes  | No  | See High Price  notes |
| 7  | Close Price  | double  | Previous day's closing price  | No  | No  | Closing prices are updated from the DB at 7:29AM ET. |
| 8  | Total Volume long  |  | Aggregated contracts traded throughout the day, including pre/post market hours. | Yes  | No  | Volume is set to zero at 3:30am ET. |
| 9  | Open  Interest | int  |  | Yes  | No |  |
| 10  | Volatility  | double  | Option Risk/Volatility  Measurement/Implied | Yes  | No  | Volatility is reset to 0 at 3:30am ET |
| 11  | Money  Intrinsic  Value | double  | The value an option would have if it were exercised today. Basically, the intrinsic value is the amount by which the strike price of an option is profitable or in-the-money as compared to the underlying stock's price in the market. | Yes  | No  | In-the-money is positive, out-of-the money is negative. |
| 12  | Expiration  Year | int |  |  |  |  |
| 13  | Multiplier  | double |  |  |  |  |
| 14  | Digits  | int  | Number of decimal places |  |  |  |
| 15  | Open Price  | double  | Day's Open Price Yes No According to industry standard, only regular session trades set the open If a stock does not trade during the regular session, then the open price is 0\.  In the pre-market session, open is blank because pre-market session trades do not set the open.  Open is set to ZERO at 7:28 ET. |  |  |  |
| 16  | Bid Size  | int  | Number of contracts for bid  | Yes  | No  | From FH |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 16/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name  | Developer Portal | Type  | Length  | Description |  |  |
| :---- | ----- | ----- | :---- | ----- | :---- | :---- |
| 17  | Ask Size  | int  | Number of contracts for ask  | Yes  | No  | From FH |
| 18  | Last Size  | int  | Number of contracts traded with last trade | Yes  | No  | Size in 100's |
| 19  | Net Change  | double  | Current Last-Prev Close  | Yes  | No  | If(close\>0)  change \= last â€“ close  Else change=0 |
| 20  | Strike Price  | double  | Contract strike price  | Yes  | No |  |
| 21  | Contract  Type | char |  |  |  |  |
| 22  | Underlying  | String |  |  |  |  |
| 23  | Expiration  Month | int\< |  |  |  |  |
| 24  | Deliverables  | String |  |  |  |  |
| 25  | Time Value  | double |  |  |  |  |
| 26  | Expiration  Day | int |  |  |  |  |
| 27  | Days to  Expiration | int |  |  |  |  |
| 28  | Delta  | double |  |  |  |  |
| 29  | Gamma  | double |  |  |  |  |
| 30  | Theta  | double |  |  |  |  |
| 31  | Vega  | double |  |  |  |  |
| 32  | Rho  | double |  |  |  |  |
| 33  | Security  Status | String  |  | Yes  | Yes  | Indicates a  symbol's current trading status:  Normal, Halted,  Closed |
| 34  | Theoretical  Option Value | double |  |  |  |  |
| 35  | Underlying Price | double |  |  |  |  |
| 36  | UV  Expiration  Type | char |  |  |  |  |
| 37  | Mark Price  | double  | Mark Price  | Yes  | Yes |  |

38 Quote Time   
long Last quote time in milliseconds   
Yes Yes The difference, 

in Long   
since Epoch   
measured in 

milliseconds, 

between the time 

an event occurs and 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 17/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name  | Developer Portal | Type  | Length  | Description |  |  |
| :---- | ----- | ----- | :---- | ----- | :---- | :---- |
|  |  |  |  |  | midnight, January 1, 1970 UTC. |  |
| 39  | Trade Time in Long | long  | Last trade time in milliseconds since Epoch | Yes  | Yes  | The difference,  measured in  milliseconds,  between the time an event occurs and midnight,  January 1, 1970 UTC. |
| 40  | Exchange  | char  | Exchange character  | Yes  | Yes  | o |
| 41  | Exchange  Name | String  | Display name of exchange  | Yes  | Yes |  |
| 42  | Last Trading Day | long  | Last Trading Day  | Yes  | Yes |  |
| 43  | Settlement  Type | char  | Settlement type character  | Yes  | Yes |  |
| 44  | Net Percent Change | double  | Net Percentage Change  | Yes  | Yes  | 4.2358 |
| 45  | Mark Price  Net Change | double  | Mark price net change  | Yes  | Yes  | 7.97 |
| 46  | Mark Price  Percent  Change | double  | Mark price percentage change  | Yes  | Yes  | 4.2358 |
| 47  | Implied Yield double |  |  |  |  |  |
| 48  | isPennyPilot  | boolean |  |  |  |  |
| 49  | Option Root  | String |  |  |  |  |
| 50  | 52 Week  High | double |  |  |  |  |
| 51  | 52 Week  Low | double |  |  |  |  |
| 52  | Indicative  Ask Price | double  |  |  |  | Only valid for index options (0 for all other options) |
| 53  | Indicative  Bid Price | double  |  |  |  | Only valid for index options (0 for all other options) |

54 Indicative   
long The latest time the indicative   
Only valid for index 

Quote Time   
bid/ask prices updated in   
options (0 for all 

milliseconds since Epoch   
other options) 

The difference, 

measured in 

milliseconds, 

between the time 

an event occurs and 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 18/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name  | Developer Portal | Type  | Length  | Description |  |  |
| :---- | ----- | ----- | :---- | ----- | :---- | :---- |
|  |  |  |  |  | midnight, January 1, 1970 UTC. |  |
| 55  | Exercise  Type | char |  |  |  |  |

3\. LEVELONE\_FUTURES 

Please refer to LEVELONE\_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE\_EQUITIES with LEVELONE\_FUTURES. Level One Futures Fields for Streamer 

| Streamer Contract  name  |  | Type  |  | Length Description |
| ----- | :---- | ----- | :---- | ----- |
| service  |  | String  |  | Variable LEVELONE\_FUTURES |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 19/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract  name Developer Portal |  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- | :---- |
| parameters  | keys  | String  |  | Variable Futures symbols in upper case and separated by commas. Schwab-standard format:  '/' \+ 'root symbol' \+ 'month code' \+ 'year code'  where month code is:  F: January  G: February  H: March  J: April  K: May  M: June  N: July  Q: August  U: September  V: October  X: November  Z: December  and year code is the last two digits of the year  Common roots:  ES: E-Mini S\&P 500  NQ: E-Mini Nasdaq 100  CL: Light Sweet Crude Oil  GC: Gold  HO: Heating Oil  BZ: Brent Crude Oil  YM: Mini Dow Jones Industrial Average |
|  |  | fields String  |  | Variable Please see the LEVELONE\_FUTURES Field Definition table below |

Response Field Definitions 

|  | Field Field Name  | Type  | Field Description | Update  Regular  Hours | Update AM/PMHours  | Notes, Examples Source |
| :---- | :---- | ----- | :---- | :---- | :---- | :---- |
| 0  | Symbol  |  | String  | Ticker  symbol in upper case. | N/A  | N/A |
| 1  | Bid Price  | double  | Current Best Bid Price  | Yes  | Yes |  |
| 2  | Ask Price  | double  | Current Best Ask Price  | Yes  | Yes |  |
| 3  | Last Price  | double  | Price at which the last trade was matched | Yes  | Yes |  |
| 4  | Bid Size  | long  | Number of contracts for bid  | Yes  | Yes |  |
| 5  | Ask Size  | long  | Number of contracts for ask  | Yes  | Yes |  |
| 6  | Bid ID  | char  | Exchange with the best bid  | Yes  | Yes Currently "?" for unknown as all quotes are CME |  |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 20/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Field Field Name  | Developer PortalType  | Field Description | Update  Regular  Hours | Update AM/PMHours  | Notes, Examples Source |
| ----- | ----- | ----- | :---- | :---- | :---- | :---- |
| 7  | Ask ID  | char  | Exchange with the best ask  | Yes  | Yes  | Currently "?" for unknown as all quotes are CME |
| 8  | Total Volume  | long  | Aggregated contracts traded throughout the day, including pre/post market hours. | Yes  |  | Yes |
| 9  | Last Size  | long  | Number of contracts traded with last trade | Yes  | Yes |  |
| 10  | Quote Time  | long  | Time of the last quote in milliseconds since epoch | Yes  | Yes |  |
| 11  | Trade Time  | long  | Time of the last trade in  milliseconds since epoch | Yes  | Yes |  |
| 12  | High Price  | double  | Day's high trade price  | Yes  | Yes |  |
| 13  | Low Price  | double  | Day's low trade price  | Yes  | Yes |  |
| 14  | Close Price  | double  | Previous day's closing price  | N/A  | N/A |  |
| 15  | Exchange ID  | char  | Primary "listing" Exchange  | N/A  | N/A  | Currently "?" for unknown as all quotes are CME |
| 16  | Description  | String  | Description of the product  | N/A  | N/A |  |
| 17  | Last ID  | char  | Exchange where last trade was executed | Yes  | Yes |  |
| 18  | Open Price  | double  | Day's Open Price  | Yes  | Yes |  |
| 19  | Net Change  | double  | Current Last-Prev Close  | Yes  | Yes  | If(close\>0)  change \= last â€“ close  else change=0 |
| 20  | Future  Percent  Change | double  | Current percent change  | Yes  | Yes  | If(close\>0)  pctChange \= (last â€“  close)/close  else pctChange=0 |
| 21  | Exchange  Name | String  | Name of exchange |  |  |  |
| 22  | Security  Status | String  | Trading status of the symbol  | Yes  | Yes  | Indicates a symbols current trading status, Normal, Halted, Closed |
| 23  | Open Interest int  |  | The total number of futures contracts that are not closed or delivered on a particular day | Yes  | Yes |  |
| 24  | Mark  | double  | Mark-to-Market value is  calculated daily using current prices to determine profit/loss | Yes  | Yes  | If lastprice is within spread, value \= lastprice  else  value=(bid+ask)/2 |
| 25  | Tick  | double  | Minimum price movement  | N/A  | N/A  | Minimum price increment of contract |
| 26  | Tick Amount  | double  | Minimum amount that the price of the market can change | N/A  | N/A  | Tick \* multiplier field |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 21/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Field Field Name  | Developer PortalType  | Field Description | Update  Regular  Hours | Update AM/PMHours  | Notes, Examples Source |
| ----- | :---- | ----- | ----- | :---- | :---- | :---- |
| 27  | Product  | String  | Futures product  | N/A  | N/A  | From Database |
| 28  | Future Price Format | String  | Display in fraction or decimal format. N/A N/A Set from FSP Config  format is \\\< numerator  decimals to display\\\>, \\\< implied denominator\>  where D=decimal format, no fractional display  Equity futures will be "D,D" to indicate pure decimal.  Fixed income futures are fractional, typically "3,32". Below is an example for  "3,32":  price=101.8203125  \=101 \+ 0.8203125 (split into whole and fractional)  \=101 \+ 26.25/32 (Multiply fractional by implied  denomiator)  \=101 \+ 26.2/32 (round to numerator decimals to display) \=101'262 (display in fractional format) |  |  |  |
| 29  | Future  Trading  Hours | String  | Trading hours  | N/A  | N/A  | days: 0 \= monday-friday, 1 \= sunday,  7 \= Saturday  0 \= \[-2000,1700\] \==\> open, close 1= \[-1530,-1630,-1700,1515\] \==\> open, close, open, close  0 \= \[-1800,1700,d,-1700,1900\] \==\> open, close, DST-flag, open, close |
| 30  | Future Is  Tradable |  | boolean Flag to indicate if this future contract is tradable | N/A  | N/A |  |
| 31  | Future  Multiplier | double  | Point value  | N/A  | N/A |  |
| 32  | Future Is  Active |  | boolean Indicates if this contract is active | Yes  | Yes |  |
| 33  | Future  Settlement  Price | double  | Closing price  | Yes  | Yes |  |
| 34  | Future Active Symbol | String  | Symbol of the active contract  | N/A  | N/A |  |
| 35  | Future  Expiration  Date | long  | Expiration date of this contract  | N/A  | N/A  | Milliseconds since epoch |
| 36  | Expiration  Style | String |  |  |  |  |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 22/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Field Field Name  | Developer PortalType  | Field Description | Update  Regular  Hours | Update AM/PMHours  | Notes, Examples Source |
| ----- | :---- | :---- | ----- | :---- | :---- | :---- |
| 37  | Ask Time  | long  | Time of the last ask-side quote in milliseconds since epoch | Yes  | Yes |  |
| 38  | Bid Time  | long  | Time of the last bid-side quote in milliseconds since epoch | Yes  | Yes |  |
| 39  | Quoted In  Session |  | boolean Indicates if this contract has quoted during the active session |  |  |  |
| 40  | Settlement  Date | long  | Expiration date of this contract  | N/A  | N/A  | Milliseconds since epoch |

For more examples on Futures Price format, see: https://www.cmegroup.com/confluence/display/EPICSANDBOX/Fractional+Pricing+- \+Display+Examples 

If the DST-flag is present for Futures Trading Hours (field 29), please see the following hours for DST days: 

https://www.cmegroup.com/confluence/display/EPICSANDBOX/Fractional+Pricing+-+Display+Examples 

4\. LEVELONE\_FUTURES\_OPTIONS 

Please refer to LEVELONE\_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE\_EQUITIES with LEVELONE\_FUTURES\_OPTIONS. 

Level One Futures Options Fields for Streamer 

| Streamer Contract  name  |  | Type  |  | Length Description |
| ----- | :---- | ----- | :---- | ----- |
| service  |  | String  |  | Variable LEVELONE\_FUTURES\_OPTIONS |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |

parameters keys String Variable Symbols in upper case and separated by commas. 

Schwab-standard format: 

'.' \+ '/' \+ 'root symbol' \+ 'month code' \+ 'year code' \+ 'Call/Put code' \+ 'Strike 

Price' 

where month code is: 

F: January 

G: February 

H: March 

J: April 

K: May 

M: June 

N: July 

Q: August 

U: September 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 23/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract  name Developer Portal |  | Type  |  | Length Description |
| ----- | :---- | ----- | :---- | :---- |
|  |  |  |  | V: October  X: November  Z: December  and year code is the last two digits of the year  e.g.: ./OZCZ23C565 |
|  |  | fields String  |  | Variable Please see the LEVELONE\_FUTURES\_OPTIONS Field Definition table below |

Response Field Definitions 

|  | Fields Field Name  | Type  | Field Description | Update  Regular  Hours | Update  AM/PM  Hours  | Notes, Examples Source |
| :---- | ----- | ----- | ----- | :---- | :---- | :---- |
| 0  | Symbol  |  | String Ticker symbol in upper case.  | N/A  | N/A |  |
| 1  | Bid Price  |  | double Current Bid Price  | Yes  | Yes |  |
| 2  | Ask Price  |  | double Current Ask Price  | Yes  | Yes |  |
| 3  | Last Price  |  | double Price at which the last trade was matched | Yes  | Yes |  |
| 4  | Bid Size  | long  | Number of contracts for bid  | Yes  | Yes |  |
| 5  | Ask Size  | long  | Number of contracts for ask  | Yes  | Yes |  |
| 6  | Bid ID  | char  | Exchange with the bid  | Yes  | Yes  | Currently "?" for unknown as all quotes are CME |
| 7  | Ask ID  | char  | Exchange with the ask  | Yes  | Yes  | Currently "?" for unknown as all quotes are CME |
| 8  | Total Volume  | long  | Aggregated contracts traded throughout the day, including pre/post market hours. | Yes  | Yes |  |
| 9  | Last Size  | long  | Number of contracts traded with last trade | Yes  | Yes |  |
| 10  | Quote Time  | long  | Trade time of the last quote in milliseconds since epoch | Yes  | Yes |  |
| 11  | Trade Time  | long  | Trade time of the last trade in milliseconds since epoch | Yes  | Yes |  |
| 12  | High Price  |  | double Day's high trade price  | Yes  | Yes  |  |
| 13  | Low Price  |  | double Day's low trade price  | Yes  | Yes |  |
| 14  | Close Price  |  | double Previous day's closing price  | N/A  | N/A |  |
| 15  | Last ID  | char  | Exchange where last trade was executed | Yes  | Yes  | Currently "?" for unknown as all quotes are CME |
| 16  | Description  | String  | Description of the product  | N/A  | N/A |  |
| 17  | Open Price  |  | double Day's Open Price  | Yes  | Yes |  |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 24/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Developer PortalFields Field Name  | Type  | Field Description | Update  Regular  Hours | Update  AM/PM  Hours  | Notes, Examples Source |
| :---- | ----- | ----- | ----- | :---- | :---- | :---- |
| 18  | Open Interest  | double |  |  |  |  |
| 19  | Mark  |  | double Mark-to-Market value is calculated daily using current prices to determine profit/loss | Yes  | Yes  | If lastprice is within spread, value \= lastprice  else  value=(bid+ask)/2 |
| 20  | Tick  |  | double Minimum price movement  | N/A  | N/A  | Minimum price increment of contract |
| 21  | Tick Amount  |  | double Minimum amount that the price of the market can change | N/A  | N/A  | Tick \* multiplier field |
| 22  | Future  Multiplier |  | double Point value  | N/A  | N/A |  |
| 23  | Future  Settlement  Price |  | double Closing price  | Yes  | Yes |  |
| 24  | Underlying  Symbol | String  | Underlying symbol  | N/A  | N/A |  |
| 25  | Strike Price  |  | double Strike Price |  |  |  |
| 26  | Future  Expiration Date | long  | Expiration date of this contract  | N/A  | N/A  | Milliseconds since epoch |
| 27  | Expiration  Style | String |  |  |  |  |
| 28  | Contract Type  | Char |  |  |  |  |
| 29  | Security Status String  |  |  | Yes  | Yes  | Indicates a symbol's current trading status: Normal,  Halted, Closed |
| 30  | Exchange  | char  | Exchange character  | Yes  | Yes |  |
| 31  | Exchange  Name | String  | Display name of exchange  | Yes  | Yes |  |

5\. LEVELONE\_FOREX 

Please refer to LEVELONE\_EQUITIES for REQUESTS and RESPONSE examples. Replace LEVELONE\_EQUITIES with LEVELONE\_FOREX. Level One Forex Request for Streamer 

| Streamer Contract  name  |  | Type  |  | Length Description |
| ----- | :---- | ----- | :---- | :---- |
| service  |  | String  |  | Variable LEVELONE\_FOREX |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable \`schwabClientCustomerId\` as found in GET User Preference endpoint |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 25/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract  name Developer Portal |  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- | ----- |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | keys  | String  |  | Variable Symbols in upper case and separated by commas.  e.g.: EUR/USD,USD/JPY,AUD/CAD |
|  |  | fields String  |  | Variable Please see the LEVELONE\_FOREX Field Definition table below |

Response Field Definitions 

|  | Fields Field Name  | Type  | Field Description | Update  Regular  Hours | Update  AM/PM  Hours  | Notes, Examples Source |
| :---- | ----- | ----- | :---- | :---- | :---- | :---- |
| 0  | Symbol  | String  | Ticker symbol in upper case.  | N/A  | N/A |  |
| 1  | Bid Price  | double  | Current Bid Price  | Yes  | Yes |  |
| 2  | Ask Price  | double  | Current Ask Price  | Yes  | Yes |  |
| 3  | Last Price d  | ouble  | Price at which the last trade was matched | Yes  | Yes |  |
| 4  | Bid Size  | long  | Number of currency pairs for bid  | Yes  | Yes |  |
| 5  | Ask Size  | long  | Number of currency pairs for ask  | Yes  | Yes |  |
| 6  | Total Volume long  |  | Aggregated currency pairs traded throughout the day, including pre/post market hours. | Yes  | Yes |  |
| 7  | Last Size  | long  | Number of currency pairs traded with last trade | Yes  | Yes |  |
| 8  | Quote Time  | long  | Trade time of the last quote in milliseconds since epoch | Yes  | Yes |  |
| 9  | Trade Time  | long  | Trade time of the last trade in milliseconds since epoch | Yes  | Yes |  |
| 10  | High Price  | double  | Day's high trade price  | Yes  | Yes |  |
| 11  | Low Price d  | ouble  | Day's low trade price  | Yes  | Yes |  |
| 12  | Close Price  | double  | Previous day's closing price  | N/A  | N/A |  |
| 13  | Exchange  | char |  |  |  |  |
| 14  | Description  | String  | Description of the product  | N/A  | N/A |  |
| 15  | Open Price  | double  | Day's Open Price  | Yes  | Yes |  |
| 16  | Net Change  | double  | Current Last-Prev Close  | Yes  | Yes  | If(close\>0)  change \= last â€“ close  else change=0 |
| 17  | Percent  Change | double  | Current percent change  | Yes  | Yes  | If(close\>0)  pctChange \= (last â€“  close)/close  else pctChange=0 |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 26/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Developer PortalFields Field Name  | Type  | Field Description | Update  Regular  Hours | Update  AM/PM  Hours  | Notes, Examples Source |
| :---- | ----- | ----- | ----- | :---- | :---- | :---- |
| 18  | Exchange  Name | String  | Name of exchange  | N/A  | N/A |  |
| 19  | Digits  | Int  | Valid decimal points  | N/A  | N/A |  |
| 20  | Security  Status | String  | Trading status of the symbol  | Yes  | Yes  | Indicates a symbols current trading status, Normal, Halted, Closed |
| 21  | Tick  | double  | Minimum price movement  | N/A  | N/A  | Minimum price increment for pair |
| 22  | Tick Amount  | double  | Minimum amount that the price of the market can change | N/A  | N/A  | Tick \* multiplier field from database |
| 23  | Product  | String  | Product name  | N/A  | N/A |  |
| 24  | Trading  Hours | String  | Trading hours  | N/A  | N/A |  |
| 25  | Is Tradable  |  | boolean Flag to indicate if this forex is tradable | N/A  | N/A |  |
| 26  | Market  Maker | String |  |  |  |  |
| 27  | 52 Week  High | double  | Higest price traded in the past 12 months, or 52 weeks | Yes  | Yes |  |
| 28  | 52 Week  Low | double  | Lowest price traded in the past 12 months, or 52 weeks | Yes  | Yes |  |
| 29  | Mark  | double  | Mark-to-Market value is calculated daily using current prices to  determine profit/loss | Yes  | Yes |  |

4\. BOOK Services 

1\. Book Common 

| Streamer Contract name  |  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- | ----- |
| service  |  | String  |  | Variable NYSE\_BOOK, NASDAQ\_BOOK, OPTIONS\_BOOK |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable \`schwabClientCustomerId\` as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifiervalue that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | keys  | String  |  | Variable Symbols in upper case and separated by commas. e.g.: AAPL,TSLA,IBM |
|  |  | fields String  |  | Variable Please see the BOOK Field Definition table below |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 27/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Response field definitions 

**Developer Portal**   
Book Fields for Streamer 

| Fields  | Field Name  | Value  | Type  | Description |
| :---- | :---- | :---- | :---- | :---- |
| 0  | Symbol  | Ticker symbol in upper case.  | String |  |
| 1  | Market Snapshot Time  | Milliseconds since Epoch  | long  | Timestamp for the data |
| 2  | Bid Side Levels  | Price Levels  | Array  | Bid side price levels |
| 3  | Ask Side Levels  | Price Levels  | Array  | Ask side price levels |

Book Price Levels Sub-Field for Streamer 

| Price Levels  Field \#  | Field Name  | Type  | Description |
| :---- | :---- | :---- | :---- |
| 0  | Price  | double  | Price for this level |
| 1  | Aggregate Size  | int  | Aggregate size for this price level |
| 2  | Market Maker Count  | int  | Number of Market Makers in this price level |
| 3  | Array of Market Makers  | Array  | Array of market maker sizes for this price level |

Book Market Makers Sub-Field for Streamer 

| Market Makers  Field \#  | Field Name  | Type  | Description |
| :---- | :---- | ----- | :---- |
| 0  | Market Maker ID  | String  | Market Maker ID |
| 1  | Size  | long  | Size of the Market Maker for this price level |
| 2  | Quote Time  | long  | Quote time in milliseconds for this Market Maker's quote |

5\. CHART Services 

1\. CHART\_EQUITY 

Chart Equity Request for Streamer 

| Streamer Contract name  |  | Type  |  | Length Description |
| ----- | :---- | ----- | :---- | ----- |
| service  |  | String  |  | Variable CHART\_EQUITY |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable 'schwabClientCustomerId' as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 28/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name Developer Portal |  | Type  |  | Length Description |
| :---- | ----- | ----- | :---- | :---- |
| parameters  | keys  | String  |  | Variable Equities symbols in upper case and separated by commas. e.g.: AAPL,TSLA,IBM |
|  |  | fields String  |  | Variable Please see the CHART\_EQUITY Field Definition table below |

Response field definitions 

| Fields | Field  Name  | Type  | Field Description | Update Regular Hours | Update AM/PM Hours | Notes, Examples Source |
| ----- | ----- | ----- | ----- | :---- | :---- | :---- |
| 0  | key  |  | String Ticker symbol in upper case.  | N/A  | N/A |  |
| 1  |  |  | Open Price double Opening price for the minute  | Yes  | Yes |  |
| 2  | High Price  |  | double Highest price for the minute  | Yes  | Yes |  |
| 3  | Low Price  |  | double Chart's lowest price for the minute | Yes  | Yes |  |
| 4  |  |  | Close Price double Closing price for the minute  | Yes  | Yes |  |
| 5  | Volume  |  | double Total volume for the minute  | Yes  | Yes |  |
| 6  | Sequence  | long  | Identifies the candle minute  | Yes  | Yes |  |
| 7  | Chart Time  | long  | Milliseconds since Epoch  | Yes  | Yes |  |
| 8  | Chart Day  | int |  |  |  |  |

2\. CHART\_FUTURES 

Chart Futures Request for Streamer 

| Streamer Contract name  |  | Type  |  | Length Description |
| ----- | :---- | ----- | :---- | ----- |
| service  |  | String  |  | Variable CHART\_FUTURES |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable 'schwabClientCustomerId' as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |

parameters keys String Variable Futures symbols in upper case and separated by commas Schwab-standard format: 

'/' \+ 'root symbol' \+ 'month code' \+ 'year code' 

where month code is: 

F: January 

G: February 

H: March 

J: April 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 29/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name Developer Portal |  | Type  |  | Length Description |
| :---- | :---- | ----- | :---- | :---- |
|  |  |  |  | K: May  M: June  N: July  Q: August  U: September  V: October  X: November  Z: December  and year code is the last two digits of the year  Common roots:  ES: E-Mini S\&P 500  NQ: E-Mini Nasdaq 100  CL: Light Sweet Crude Oil  GC: Gold  HO: Heating Oil  BZ: Brent Crude Oil  YM: Mini Dow Jones Industrial Average |
|  |  | fields String  |  | Variable Please see the CHART\_FUTURES Field Definition table below |

Field response definitions 

| Fields | Field  Name  | Type  | Field Description | Update Regular Hours | Update AM/PM Hours | Notes, Examples Source |
| ----- | ----- | ----- | ----- | :---- | :---- | :---- |
| 0  | key  |  | String Ticker symbol in upper case.  | N/A  | N/A |  |
| 1  | Chart Time long  |  | Milliseconds since Epoch  | Yes  | Yes |  |
| 2  |  |  | Open Price double Opening price for the minute  | Yes  | Yes |  |
| 3  | High Price  |  | double Highest price for the minute  | Yes  | Yes |  |
| 4  | Low Price  |  | double Chart's lowest price for the minute | Yes  | Yes |  |
| 5  |  |  | Close Price double Closing price for the minute  | Yes  | Yes |  |
| 6  | Volume  |  | double Total volume for the minute  | Yes  | Yes |  |

6\. SCREENER services 

1\. Screener Common 

Screener Request for Streamer 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 30/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Streamer Contract name Developer Portal |  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- | ----- |
| service  |  | String  |  | Variable SCREENER\_EQUITY, SCREENER\_OPTION |
| command  |  | String  |  | Variable SUBS, UNSUBS, ADD, VIEW |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable 'schwabClientCustomerId' as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | keys  | String  |  | Variable Symbols in upper case and separated by commas. (PREFIX)\_(SORTFIELD)\_(FREQUENCY) where PREFIX is:  Indices: $COMPX $DJI, $SPX, INDEX\_ALL  Exchanges: NYSE, NASDAQ, OTCBB, EQUITY\_ALL  Option: OPTION\_PUT, OPTION\_CALL, OPTION\_ALL  and sortField is:  VOLUME, TRADES, PERCENT\_CHANGE\_UP,  PERCENT\_CHANGE\_DOWN, AVERAGE\_PERCENT\_VOLUME  and frequency is:  0, 1, 5, 10, 30 60 minutes (0 is for all day) |
|  |  | fields String  |  | Variable Please see the SCREENER Field Definition table below |

Response field definitions 

|  | Index Field  | Type  | Description  | Values |
| :---- | ----- | ----- | :---- | :---- |
| 0  | symbol  | String  | The symbol used to look up either actives, gainers or losers | Subscribed or requested symbol |
| 1  | timestamp long  |  | Market snapshot timestamp in milliseconds since Epoch | 12345613123 |
| 2  | sortField  | String  | Field to sort on  | VOLUME, TRADES, PERCENT\_CHANGE\_UP,  PERCENT\_CHANGE\_DOWN, AVERAGE\_PERCENT\_VOLUME |
| 3  |  |  | frequency Integer Frequency of data to sort  | 0, 1, 5, 10, 30 60 minutes (0 is for all day) |
| 4  | Items  | Array  |  | Refer to the field table below |

| Field  | Type  | Description |
| :---- | :---- | :---- |
| description  | String  | Description of instrument |
| lastPrice  | double  | Last trade price (up to 2 decimal places) |
| marketShare  | double  | Market share percentage of instrument (up to 2 decimal places) |

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 31/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

| Field Developer Portal | Type  | Description |
| ----- | :---- | :---- |
| netChange  | double  | Net change value (up to 2 decimal places) |
| netPercentChange  | double  | Net percent change value (up to 4 decimal places) |
| symbol  | String  | Stock or Option symbol |
| totalVolume  | long  | Total volume for the day |
| trades  | long  | Number of trades for the frequency requested |
| volume  | long  | Volume for the frequency requested |

7\. ACCOUNT services 

1\. ACCT\_ACTIVITY 

Account Activity Request for Streamer 

| Streamer Contract name  |  | Type  |  | Length Description |
| ----- | ----- | ----- | :---- | ----- |
| service  |  | String  |  | Variable ACCOUNT\_ACTIVITY |
| command  |  | String  |  | Variable SUBS, UNSUBS |
| requestid  |  |  |  | Integer Variable Unique number that will identify this request. |
| SchwabClientCustomerId  |  | String  |  | Variable 'schwabClientCustomerId' as found in GET User Preference endpoint |
| SchwabClientCorrelId  |  | String  |  | Variable Unique identifier value that is attached to requests and messages that allow reference to a particular transaction or event chain. |
| parameters  | keys  | String  |  | Variable A client-provided string that streamer will populate updates with. Only first key is used if multiple are provided. |
|  |  | fields String  |  | Variable "0" expected |

Example: 

{ 

"requests": \[ 

{ 

"service": "ACCT\_ACTIVITY", 

"requestid": "2", 

"command": "SUBS", 

"SchwabClientCustomerId": "Someone", 

"SchwabClientCorrelId": "f308b89-19a7-2d18-4a0a-1c5e7120336", 

"parameters": { 

"keys": "Account Activity", 

"fields": "0,1,2,3" 

} 

} 

\] 

} 

Response 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 32/33  
2/26/26, 6:52 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

|  | Fields Field Name Type Developer Portal |  | Value |
| ----- | ----- | ----- | :---- |
| "seq"  | Sequence  |  | Integer This field identifies the message number. If client reconnects and receives the same seq number again, it can choose to ignore the duplicate. |
| "key"  | Key  | String  | Passed back to the client from the request to identify a subscription this response belongs to. |
| 1  | Account  | String  | Account Number that the activity occurred on. |
| 2  | Message  Type | String  | Message Type that dictates the format of the Message Data field. |
| 3  | Message  Data | String  | The core data for the message. Either JSON-formatted data describing the update, NULL in some cases, or plain text in case of ERROR. |

Terms Of Use | Privacy Notice 

© 2026 Charles Schwab & Co., Inc. All rights reserved. Member SIPC. Unauthorized access is prohibited. Usage is monitored. 

https://developer.schwab.com/products/trader-api--individual/details/documentation/Market Data Production 33/33