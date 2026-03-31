2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home   
~~API P~~roducts   
~~API Products~~ 

User Guides   
T~~rader API \-~~ Individual 

Accounts and Trading Production 

**Accounts and Trading Production** 

Specifications 

Documentation 

APIs to access Account Balances & Positions, to perform trading activities 

Trader API \- Account Access and User Preferences 

**1.0.0** 

**OAS3** 

Schwab Trader API access to Account, Order entry and User Preferences 

Contact Schwab Trader API team 

Servers   
https://api.schwabapi.com/trader/v1 

Authorize 

Accounts 

GET/accounts/accountNumbers Get list of account numbers and their encrypted values 

Account numbers in plain text cannot be used outside of headers or request/response bodies. As the first step consumers must invoke this service to retrieve the list of plain text/encrypted value pairs, and use encrypted account values for all subsequent calls for any accountNumber request. 

Parameters 

Try it out 

No parameters 

Responses 

Code Description Links 

200 List of valid "accounts", matching the provided input parameters. 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

file:///Users/licaris/Downloads/account\_access.html 1/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\[   
**Schwab**   
{   
**Logo Developer Portal**   
"accountNumber": "string", 

Home   
}   
\]   
"hashValue": "string" 

API Products 

User Guides   
~~Heade~~rs: 

Name Description Type 

Schwab-Client-CorrelId Correlation Id. Auto generated string An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string 404 An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

No   
links 

file:///Users/licaris/Downloads/account\_access.html 2/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
{   
**Schwab**   
"message": "string",   
**Logo Developer Portal**   
"errors": \[ 

Home   
\]   
}   
"string" 

API Products 

User Guides   
~~Heade~~rs: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

GET/accounts Get linked account(s) balances and positions for the logged in user. 

All the linked account information for the user logged in. The balances on these accounts are displayed by default however the positions on these accounts will be displayed based on the "positions" flag. 

Parameters 

Try it out 

Name Description 

This allows one to determine which fields they want returned. Possible value in this String can be: 

fields string   
positions   
Example:   
fields=positions   
(query)   
fields 

Responses 

Code Description Links 

200 List of valid "accounts", matching the provided input parameters. 

Media type   
application/json   
No   
links 

file:///Users/licaris/Downloads/account\_access.html 3/102  
2/26/26, 6:59 PMTraderAPI-Individual | Products | CharlesSchwabDeveloperPortal CodeDescriptionLinks DeveloperPortal   
**Charles**   
ControlsAcceptheader.   
**Schwab**   
**LogoDeveloperPortal**   
Example Value 

Home   
Schema 

\[   
API Products   
{   
User Guides   
~~"~~securitiesAccount": {   
"accountNumber": "string",   
"roundTrips": 0,   
"isDayTrader": false,   
"isClosingOnlyRestricted": false,   
"pfcbFlag": false,   
"positions": \[   
{ 

}   
\],   
"shortQuantity": 0,   
"averagePrice": 0,   
"currentDayProfitLoss": 0,   
"currentDayProfitLossPercentage": 0, "longQuantity": 0,   
"settledLongQuantity": 0,   
"settledShortQuantity": 0,   
"agedQuantity": 0,   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string",   
"instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE"   
},   
"marketValue": 0,   
"maintenanceRequirement": 0, "averageLongPrice": 0,   
"averageShortPrice": 0,   
"taxLotAverageLongPrice": 0, "taxLotAverageShortPrice": 0, "longOpenProfitLoss": 0,   
"shortOpenProfitLoss": 0,   
"previousSessionLongQuantity": 0, "previousSessionShortQuantity": 0, "currentDayCost": 0 

"initialBalances": {   
"accruedInterest": 0,   
"availableFundsNonMarginableTrade": 0,   
"bondValue": 0,   
"buyingPower": 0,   
"cashBalance": 0,   
"cashAvailableForTrading": 0,   
"cashReceipts": 0,   
"dayTradingBuyingPower": 0,   
"dayTradingBuyingPowerCall": 0,   
"dayTradingEquityCall": 0,   
"equity": 0,   
"equityPercentage": 0,   
"liquidationValue": 0,   
"longMarginValue": 0,   
"longOptionMarketValue": 0,   
"longStockValue": 0,   
"maintenanceCall": 0,   
"maintenanceRequirement": 0,   
"margin": 0,   
"marginEquity": 0,   
"moneyMarketFund": 0,   
"mutualFundValue": 0,   
"regTCall": 0,   
"shortMarginValue": 0,   
"shortOptionMarketValue": 0,   
"shortStockValue": 0,   
"totalCash": 0,   
"isInCall": 0,   
"unsettledCash": 0,   
"pendingDeposits": 0,   
"marginBalance": 0,   
"shortBalance": 0,   
"accountValue": 0   
},   
"currentBalances": {   
"availableFunds": 0,   
"availableFundsNonMarginableTrade": 0,   
"buyingPower": 0,   
"buyingPowerNonMarginableTrade": 0,   
"dayTradingBuyingPower": 0,   
"dayTradingBuyingPowerCall": 0,   
"equity": 0,   
"equityPercentage": 0,   
"longMarginValue": 0,   
"maintenanceCall": 0,   
"maintenanceRequirement": 0,   
"marginBalance": 0,   
"regTCall": 0,   
"shortBalance": 0,   
"shortMarginValue": 0, 

file:///Users/licaris/Downloads/account\_access.html4/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab** 

"sma": 0,   
"isInCall": 0,   
**Logo Developer Portal**   
"stockBuyingPower": 0,   
"optionBuyingPower": 0 

Home   
},   
"projectedBalances": { 

API Products User Guides 

}   
"availableFunds": 0,   
"availableFundsNonMarginableTrade": 0, "buyingPower": 0,   
"buyingPowerNonMarginableTrade": 0, "dayTradingBuyingPower": 0,   
"dayTradingBuyingPowerCall": 0, "equity": 0,   
"equityPercentage": 0,   
"longMarginValue": 0,   
"maintenanceCall": 0,   
"maintenanceRequirement": 0,   
"marginBalance": 0,   
"regTCall": 0,   
"shortBalance": 0,   
"shortMarginValue": 0,   
"sma": 0,   
"isInCall": 0,   
"stockBuyingPower": 0,   
"optionBuyingPower": 0 

}   
\]   
} 

Headers: 

Name Description Type Schwab-Client-CorrelId Correlation Id. Auto generated string An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

403 An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

No   
links 

file:///Users/licaris/Downloads/account\_access.html 5/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
{   
**Schwab**   
"message": "string",   
**Logo Developer Portal**   
"errors": \[ 

Home   
\]   
}   
"string" 

API Products 

User Guides   
~~Heade~~rs: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

GET/accounts/{accountNumber}   
Get a specific account balance and positions for the logged in user. 

Specific account information with balances and positions. The balance information on these accounts is displayed by default but Positions will be returned based on the "positions" flag. 

file:///Users/licaris/Downloads/account\_access.html 6/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Parameters   
Developer Portal   
**Charles**   
**Schwab**   
Try it out   
**Logo Developer Portal**   
Name Description 

The encrypted ID of the account   
accountNumber \*   
Home   
string   
API Products ~~(path)~~   
User Guides 

fields   
string   
(query) 

Responses   
accountNumber 

This allows one to determine which fields they want returned. Possible values in this String can be: positions   
Example:   
fields=positions 

fields 

Code Description Links 

200 A valid account, matching the provided input parameters 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

{   
"securitiesAccount": {   
"accountNumber": "string",   
"roundTrips": 0,   
"isDayTrader": false,   
"isClosingOnlyRestricted": false, "pfcbFlag": false,   
"positions": \[   
{ 

}   
\],   
"shortQuantity": 0,   
"averagePrice": 0,   
"currentDayProfitLoss": 0,   
"currentDayProfitLossPercentage": 0, "longQuantity": 0,   
"settledLongQuantity": 0,   
"settledShortQuantity": 0,   
"agedQuantity": 0,   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string",   
"instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE"   
},   
"marketValue": 0,   
"maintenanceRequirement": 0, "averageLongPrice": 0,   
"averageShortPrice": 0,   
"taxLotAverageLongPrice": 0, "taxLotAverageShortPrice": 0, "longOpenProfitLoss": 0,   
"shortOpenProfitLoss": 0,   
"previousSessionLongQuantity": 0, "previousSessionShortQuantity": 0, "currentDayCost": 0 

"initialBalances": {   
"accruedInterest": 0,   
"availableFundsNonMarginableTrade": 0,   
"bondValue": 0,   
"buyingPower": 0,   
"cashBalance": 0,   
"cashAvailableForTrading": 0,   
"cashReceipts": 0,   
"dayTradingBuyingPower": 0,   
"dayTradingBuyingPowerCall": 0,   
"dayTradingEquityCall": 0,   
"equity": 0,   
"equityPercentage": 0,   
"liquidationValue": 0,   
"longMarginValue": 0,   
"longOptionMarketValue": 0,   
"longStockValue": 0,   
"maintenanceCall": 0,   
"maintenanceRequirement": 0,   
"margin": 0,   
"marginEquity": 0,   
"moneyMarketFund": 0,   
"mutualFundValue": 0,   
"regTCall": 0,   
"shortMarginValue": 0, 

file:///Users/licaris/Downloads/account\_access.html 7/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"shortOptionMarketValue": 0, "shortStockValue": 0,   
**Logo Developer Portal**   
"totalCash": 0,   
"isInCall": 0, 

Home   
"unsettledCash": 0, "pendingDeposits": 0, "marginBalance": 0,   
API Products   
"shortBalance": 0,   
User Guides   
"accountValue": 0   
},   
"currentBalances": {   
"availableFunds": 0,   
"availableFundsNonMarginableTrade": 0, "buyingPower": 0,   
"buyingPowerNonMarginableTrade": 0, "dayTradingBuyingPower": 0,   
"dayTradingBuyingPowerCall": 0, "equity": 0,   
"equityPercentage": 0,   
"longMarginValue": 0,   
"maintenanceCall": 0,   
"maintenanceRequirement": 0,   
"marginBalance": 0,   
"regTCall": 0,   
"shortBalance": 0,   
"shortMarginValue": 0,   
"sma": 0,   
"isInCall": 0,   
"stockBuyingPower": 0,   
"optionBuyingPower": 0   
},   
"projectedBalances": {   
"availableFunds": 0,   
"availableFundsNonMarginableTrade": 0, "buyingPower": 0,   
"buyingPowerNonMarginableTrade": 0, "dayTradingBuyingPower": 0,   
"dayTradingBuyingPowerCall": 0, "equity": 0,   
"equityPercentage": 0,   
"longMarginValue": 0,   
"maintenanceCall": 0,   
"maintenanceRequirement": 0,   
"marginBalance": 0,   
"regTCall": 0,   
"shortBalance": 0,   
"shortMarginValue": 0,   
"sma": 0,   
"isInCall": 0,   
"stockBuyingPower": 0, 

}   
}   
}   
"optionBuyingPower": 0 

Headers: 

Name Description Type Schwab-Client-CorrelId Correlation Id. Auto generated string An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

401 An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use   
for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema   
No   
links 

file:///Users/licaris/Downloads/account\_access.html 8/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
{   
**Schwab**   
"message": "string",   
**Logo Developer Portal**   
"errors": \[ 

Home   
\]   
}   
"string" 

API Products 

User Guides   
~~Heade~~rs: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 503 An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

No   
links 

file:///Users/licaris/Downloads/account\_access.html 9/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
{   
**Schwab**   
"message": "string",   
**Logo Developer Portal**   
"errors": \[ 

Home   
\]   
}   
"string" 

API Products 

User Guides   
~~Heade~~rs: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

Orders 

GET/accounts/{accountNumber}/orders Get all orders for a specific account. 

All orders for a specific account. Orders retrieved can be filtered based on input parameters below. Maximum date range is 1 year. Parameters 

Try it out 

Name Description 

accountNumber \* string   
(path) 

maxResults   
integer($int64) (query)   
The encrypted ID of the account 

accountNumber 

The max number of orders to retrieve. Default is 3000\. 

maxResults 

Specifies that no orders entered before this time should be returned. Valid ISO-8601 formats are :   
fromEnteredTime \*   
string   
(query) 

toEnteredTime \* string   
(query) 

status   
string   
(query) 

Responses   
yyyy-MM-dd'T'HH:mm:ss.SSSZ Example fromEnteredTime is '2024-03-29T00:00:00.000Z'. 'toEnteredTime' must also be set. 

fromEnteredTime 

Specifies that no orders entered after this time should be returned.Valid ISO-8601 formats are : yyyy-MM-dd'T'HH:mm:ss.SSSZ. Example toEnteredTime is '2024-04-28T23:59:59.000Z'. 'fromEnteredTime' must also be set. 

toEnteredTime 

Specifies that only orders of this status should be returned. 

Available values : AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME, PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN 

\-- 

Code Description Links 

200 A List of orders for the account, matching the provided input parameters 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

\[   
{   
"session": "NORMAL",   
"duration": "DAY",   
"orderType": "MARKET",   
"cancelTime": "2026-02-27T01:56:40.456Z", "complexOrderStrategyType": "NONE", "quantity": 0,   
"filledQuantity": 0,   
"remainingQuantity": 0,   
"requestedDestination": "INET",   
"destinationLinkName": "string",   
"releaseTime": "2026-02-27T01:56:40.456Z", "stopPrice": 0,   
"stopPriceLinkBasis": "MANUAL", 

file:///Users/licaris/Downloads/account\_access.html 10/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"stopPriceLinkType": "VALUE", "stopPriceOffset": 0,   
**Logo Developer Portal**   
"stopType": "STANDARD",   
"priceLinkBasis": "MANUAL", 

Home   
"priceLinkType": "VALUE", "price": 0,   
"taxLotMethod": "FIFO",   
API Products   
~~"o~~rderLegCollection": \[   
User Guides   
{ } 

"orderLegType": "EQUITY", "legId": 0,   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string", "instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE" },   
"instruction": "BUY",   
"positionEffect": "OPENING", "quantity": 0,   
"quantityType": "ALL\_SHARES", "divCapGains": "REINVEST", "toSymbol": "string" 

\],   
"activationPrice": 0,   
"specialInstruction": "ALL\_OR\_NONE", "orderStrategyType": "SINGLE",   
"orderId": 0,   
"cancelable": false,   
"editable": false,   
"status": "AWAITING\_PARENT\_ORDER", "enteredTime": "2026-02-27T01:56:40.456Z", "closeTime": "2026-02-27T01:56:40.456Z", "tag": "string",   
"accountNumber": 0,   
"orderActivityCollection": \[   
{   
"activityType": "EXECUTION", "executionType": "FILL", "quantity": 0,   
"orderRemainingQuantity": 0, "executionLegs": \[ 

}   
\],   
{ 

}   
\]   
"legId": 0,   
"price": 0,   
"quantity": 0,   
"mismarkedQuantity": 0,   
"instrumentId": 0,   
"time": "2026-02-27T01:56:40.456Z" 

}   
\]   
"replacingOrderCollection": \[ "string"   
\],   
"childOrderStrategies": \[ "string"   
\],   
"statusDescription": "string" 

Headers: 

Name Description Type Schwab-Client-CorrelId Correlation Id. Auto generated string An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

file:///Users/licaris/Downloads/account\_access.html 11/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use **Schwab**   
for trading that are registered with the provided third party application   
**Logo Developer Portal**   
Media type   
Home   
application/json 

API Products   
~~E~~xample Value   
User Guides   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

file:///Users/licaris/Downloads/account\_access.html 12/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
An error message indicating server has a temporary problem responding   
**Schwab**   
**Logo Developer Portal**   
Media type   
application/json   
Home 

Example Value   
API Products   
Schema   
User Guides 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string POST/accounts/{accountNumber}/orders   
Place order for a specific account. 

Place an order for a specific account. 

Parameters 

Try it out 

Name Description 

The encrypted ID of the account   
accountNumber \*   
string   
(path) 

Request body application/json 

accountNumber 

The new Order Object. 

Example Value 

Schema 

{ 

"session": "NORMAL", 

"duration": "DAY", 

"orderType": "MARKET", 

"cancelTime": "2026-02-27T01:56:40.462Z", 

"complexOrderStrategyType": "NONE", 

"quantity": 0, 

"filledQuantity": 0, 

"remainingQuantity": 0, 

"destinationLinkName": "string", 

"releaseTime": "2026-02-27T01:56:40.462Z", 

"stopPrice": 0, 

"stopPriceLinkBasis": "MANUAL", 

"stopPriceLinkType": "VALUE", 

"stopPriceOffset": 0, 

"stopType": "STANDARD", 

"priceLinkBasis": "MANUAL", 

"priceLinkType": "VALUE", 

"price": 0, 

"taxLotMethod": "FIFO", 

"orderLegCollection": \[ 

{ 

"orderLegType": "EQUITY", 

"legId": 0, 

file:///Users/licaris/Downloads/account\_access.html 13/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal "instrument": {   
Developer Portal   
**Charles Schwab**   
"cusip": "string", 

**Logo Developer Portal**   
"symbol": "string", 

"description": "string",   
Home   
"instrumentId": 0,   
API Products   
~~"~~netChange": 0,   
User Guides   
~~"~~type": "SWEEP\_VEHICLE" 

}, 

"instruction": "BUY", 

"positionEffect": "OPENING", 

"quantity": 0, 

"quantityType": "ALL\_SHARES", 

"divCapGains": "REINVEST", 

"toSymbol": "string" 

} 

\], 

"activationPrice": 0, 

"specialInstruction": "ALL\_OR\_NONE", 

"orderStrategyType": "SINGLE", 

"orderId": 0, 

"cancelable": false, 

"editable": false, 

"status": "AWAITING\_PARENT\_ORDER", 

"enteredTime": "2026-02-27T01:56:40.462Z", 

"closeTime": "2026-02-27T01:56:40.462Z", 

"accountNumber": 0, 

"orderActivityCollection": \[ 

{ 

"activityType": "EXECUTION", 

"executionType": "FILL", 

"quantity": 0, 

"orderRemainingQuantity": 0, 

"executionLegs": \[ 

{ 

"legId": 0, 

"price": 0, 

"quantity": 0, 

"mismarkedQuantity": 0, 

"instrumentId": 0, 

"time": "2026-02-27T01:56:40.462Z" 

} 

\] 

} 

\], 

"replacingOrderCollection": \[ 

"string" 

\], 

"childOrderStrategies": \[ 

"string" 

\], 

"statusDescription": "string" 

} 

Responses 

Code Description Links 

201 Empty response body if an order was successfully placed/created. Media typeControls Accept header. 

Headers:   
No   
links 

file:///Users/licaris/Downloads/account\_access.html 14/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type 

Schwab-Client-CorrelId Correlation Id. Auto generated string   
**Logo Developer Portal**   
Location Link to the newly created order if order was successfully created. string 

Home   
An error message indicating the validation problem with the request. 

API Products   
~~Media ty~~pe   
User Guides   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string 404 An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[ 

\]   
}   
"string" 

Headers: 

file:///Users/licaris/Downloads/account\_access.html 15/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string **Logo Developer Portal** 

Home   
An error message indicating there was an unexpected server error 

API Products   
Media type   
application/json   
User Guides 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string GET/accounts/{accountNumber}/orders/{orderId}   
Get a specific order by its ID, for a specific account 

Get a specific order by its ID, for a specific account Parameters 

Try it out 

Name Description 

accountNumber \*   
string   
(path) 

orderId \*   
integer($int64) (path) 

Responses   
The encrypted ID of the account 

accountNumber 

The ID of the order being retrieved. orderId 

Code Description Links 

200 An order object, matching the input parameters 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

{   
"session": "NORMAL", "duration": "DAY", "orderType": "MARKET", 

file:///Users/licaris/Downloads/account\_access.html 16/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
"cancelTime": "2026-02-27T01:56:40.467Z",   
**Schwab**   
"complexOrderStrategyType": "NONE",   
**Logo Developer Portal**   
"quantity": 0,   
"filledQuantity": 0, 

Home   
"remainingQuantity": 0,   
"requestedDestination": "INET", "destinationLinkName": "string",   
API Products   
~~"rel~~easeTime": "2026-02-27T01:56:40.467Z", "stopPrice": 0,   
User Guides   
"stopPriceLinkBasis": "MANUAL",   
"stopPriceLinkType": "VALUE",   
"stopPriceOffset": 0,   
"stopType": "STANDARD",   
"priceLinkBasis": "MANUAL",   
"priceLinkType": "VALUE",   
"price": 0,   
"taxLotMethod": "FIFO",   
"orderLegCollection": \[   
{ 

}   
\],   
"orderLegType": "EQUITY", "legId": 0,   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string", "instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE" },   
"instruction": "BUY",   
"positionEffect": "OPENING", "quantity": 0,   
"quantityType": "ALL\_SHARES", "divCapGains": "REINVEST", "toSymbol": "string" 

"activationPrice": 0,   
"specialInstruction": "ALL\_OR\_NONE", "orderStrategyType": "SINGLE",   
"orderId": 0,   
"cancelable": false,   
"editable": false,   
"status": "AWAITING\_PARENT\_ORDER", "enteredTime": "2026-02-27T01:56:40.467Z", "closeTime": "2026-02-27T01:56:40.467Z", "tag": "string",   
"accountNumber": 0,   
"orderActivityCollection": \[   
{   
"activityType": "EXECUTION", "executionType": "FILL", "quantity": 0,   
"orderRemainingQuantity": 0, "executionLegs": \[ 

}   
\],   
{ 

}   
\]   
"legId": 0,   
"price": 0,   
"quantity": 0,   
"mismarkedQuantity": 0,   
"instrumentId": 0,   
"time": "2026-02-27T01:56:40.467Z" 

}   
"replacingOrderCollection": \[ "string"   
\],   
"childOrderStrategies": \[ "string"   
\],   
"statusDescription": "string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelId Correlation Id. Auto generated string 400 An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[   
"string" 

file:///Users/licaris/Downloads/account\_access.html 17/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\]   
**Schwab**   
}   
**Logo Developer Portal** 

Home   
Headers: 

API Products User Guides   
Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 500 An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[   
"string" 

file:///Users/licaris/Downloads/account\_access.html 18/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\]   
**Schwab**   
}   
**Logo Developer Portal** 

Home   
Headers: 

API Products User Guides   
Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string DELETE/accounts/{accountNumber}/orders/{orderId}   
Cancel an order for a specific account 

Cancel a specific order for a specific account 

Parameters 

Try it out 

Name Description 

The encrypted ID of the account   
accountNumber \*   
string   
(path) 

orderId \*   
integer($int64) (path) 

Responses   
accountNumber 

The ID of the order being cancelled orderId 

Code Description Links Empty response body if an order was successfully canceled. 

Media typeControls Accept header. 

200   
Headers: 

Name Description Type Schwab-Client-CorrelId Correlation Id. Auto generated string An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema   
No   
links 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

file:///Users/licaris/Downloads/account\_access.html 19/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use **Schwab**   
for trading that are registered with the provided third party application   
**Logo Developer Portal**   
Media type   
Home   
application/json 

API Products   
~~E~~xample Value   
User Guides   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

file:///Users/licaris/Downloads/account\_access.html 20/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
An error message indicating server has a temporary problem responding   
**Schwab**   
**Logo Developer Portal**   
Media type   
application/json   
Home 

Example Value   
API Products   
Schema   
User Guides 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

PUT/accounts/{accountNumber}/orders/{orderId}   
Replace order for a specific account 

Replace an existing order for an account. The existing order will be replaced by the new order. Once replaced, the old order will be canceled and a new order will be created. 

Parameters 

Try it out 

Name Description 

The encrypted ID of the account   
accountNumber \*   
string   
(path) 

orderId \*   
integer($int64) (path) 

Request body application/json 

The Order Object.   
accountNumber 

The ID of the order being retrieved. orderId 

Example Value 

Schema 

{ 

"session": "NORMAL", 

"duration": "DAY", 

"orderType": "MARKET", 

"cancelTime": "2026-02-27T01:56:40.473Z", 

"complexOrderStrategyType": "NONE", 

"quantity": 0, 

"filledQuantity": 0, 

"remainingQuantity": 0, 

"destinationLinkName": "string", 

"releaseTime": "2026-02-27T01:56:40.473Z", 

"stopPrice": 0, 

"stopPriceLinkBasis": "MANUAL", 

"stopPriceLinkType": "VALUE", 

"stopPriceOffset": 0, 

"stopType": "STANDARD", 

"priceLinkBasis": "MANUAL", 

"priceLinkType": "VALUE", 

"price": 0, 

"taxLotMethod": "FIFO", 

"orderLegCollection": \[ 

file:///Users/licaris/Downloads/account\_access.html 21/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal {   
Developer Portal   
**Charles**   
"orderLegType": "EQUITY",   
**Schwab**   
**Logo Developer Portal**   
"legId": 0, 

"instrument": {   
Home   
"cusip": "string",   
API Products   
~~"~~symbol": "string",   
User Guides   
~~"~~description": "string", 

"instrumentId": 0, 

"netChange": 0, 

"type": "SWEEP\_VEHICLE" 

}, 

"instruction": "BUY", 

"positionEffect": "OPENING", 

"quantity": 0, 

"quantityType": "ALL\_SHARES", 

"divCapGains": "REINVEST", 

"toSymbol": "string" 

} 

\], 

"activationPrice": 0, 

"specialInstruction": "ALL\_OR\_NONE", 

"orderStrategyType": "SINGLE", 

"orderId": 0, 

"cancelable": false, 

"editable": false, 

"status": "AWAITING\_PARENT\_ORDER", 

"enteredTime": "2026-02-27T01:56:40.473Z", 

"closeTime": "2026-02-27T01:56:40.473Z", 

"accountNumber": 0, 

"orderActivityCollection": \[ 

{ 

"activityType": "EXECUTION", 

"executionType": "FILL", 

"quantity": 0, 

"orderRemainingQuantity": 0, 

"executionLegs": \[ 

{ 

"legId": 0, 

"price": 0, 

"quantity": 0, 

"mismarkedQuantity": 0, 

"instrumentId": 0, 

"time": "2026-02-27T01:56:40.473Z" 

} 

\] 

} 

\], 

"replacingOrderCollection": \[ 

"string" 

\], 

"childOrderStrategies": \[ 

"string" 

\], 

"statusDescription": "string" 

} 

Responses 

Code Description Links 

201 Empty response body if an order was successfully replaced/created. Media typeControls Accept header.   
No   
links 

file:///Users/licaris/Downloads/account\_access.html 22/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
Headers:   
**Schwab**   
**Logo Developer Portal**   
Name Description Type 

Home   
Schwab-Client-CorrelId Correlation Id. Auto generated string Location Link to the newly created order if order was successfully created. string   
API Products 

User Guides   
~~An erro~~r message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string 404 An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[   
"string" 

file:///Users/licaris/Downloads/account\_access.html 23/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
\]   
**Schwab**   
}   
**Logo Developer Portal** 

Home   
Headers: 

API Products User Guides   
Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

GET/orders Get all orders for all accounts 

Get all orders for all accounts 

Parameters 

Try it out 

Name Description 

maxResults integer($int64) (query)   
The max number of orders to retrieve. Default is 3000\. 

maxResults 

Specifies that no orders entered before this time should be returned. Valid ISO-8601 formats are- yyyy-MM   
fromEnteredTime \*   
string   
(query) 

toEnteredTime \* string   
(query) 

status   
string   
(query)   
dd'T'HH:mm:ss.SSSZ Date must be within 60 days from today's date. 'toEnteredTime' must also be set. 

fromEnteredTime 

Specifies that no orders entered after this time should be returned.Valid ISO-8601 formats are \- yyyy-MM dd'T'HH:mm:ss.SSSZ. 'fromEnteredTime' must also be set. 

toEnteredTime 

Specifies that only orders of this status should be returned. 

Available values : AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME, PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN 

\-- 

file:///Users/licaris/Downloads/account\_access.html 24/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Responses   
Developer Portal   
**Charles**   
**Schwab**   
Code Description Links **Logo Developer Portal**   
200 A List of orders for the specified account or if its not mentioned, for all the linked accounts, matching the provided input No   
Home   
parameters.   
links 

API Products   
Media type   
application/json   
User Guides   
~~Control~~s Accept header. 

Example Value   
Schema 

\[   
{   
"session": "NORMAL",   
"duration": "DAY",   
"orderType": "MARKET",   
"cancelTime": "2026-02-27T01:56:40.478Z", "complexOrderStrategyType": "NONE", "quantity": 0,   
"filledQuantity": 0,   
"remainingQuantity": 0,   
"requestedDestination": "INET",   
"destinationLinkName": "string",   
"releaseTime": "2026-02-27T01:56:40.478Z", "stopPrice": 0,   
"stopPriceLinkBasis": "MANUAL",   
"stopPriceLinkType": "VALUE",   
"stopPriceOffset": 0,   
"stopType": "STANDARD",   
"priceLinkBasis": "MANUAL",   
"priceLinkType": "VALUE",   
"price": 0,   
"taxLotMethod": "FIFO",   
"orderLegCollection": \[   
{ 

}   
\],   
"orderLegType": "EQUITY", "legId": 0,   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string", "instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE" },   
"instruction": "BUY",   
"positionEffect": "OPENING", "quantity": 0,   
"quantityType": "ALL\_SHARES", "divCapGains": "REINVEST", "toSymbol": "string" 

"activationPrice": 0,   
"specialInstruction": "ALL\_OR\_NONE", "orderStrategyType": "SINGLE",   
"orderId": 0,   
"cancelable": false,   
"editable": false,   
"status": "AWAITING\_PARENT\_ORDER", "enteredTime": "2026-02-27T01:56:40.478Z", "closeTime": "2026-02-27T01:56:40.478Z", "tag": "string",   
"accountNumber": 0,   
"orderActivityCollection": \[   
{   
"activityType": "EXECUTION", "executionType": "FILL", "quantity": 0,   
"orderRemainingQuantity": 0, "executionLegs": \[ 

}   
\],   
{ 

}   
\]   
"legId": 0,   
"price": 0,   
"quantity": 0,   
"mismarkedQuantity": 0,   
"instrumentId": 0,   
"time": "2026-02-27T01:56:40.478Z" 

}   
\]   
"replacingOrderCollection": \[ "string"   
\],   
"childOrderStrategies": \[ "string"   
\],   
"statusDescription": "string" 

file:///Users/licaris/Downloads/account\_access.html 25/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
Headers:   
**Schwab**   
**Logo Developer Portal**   
Name Description Type 

Home   
Schwab-Client-CorrelId Correlation Id. Auto generated string 

API Products   
An error message indicating the validation problem with the request. User Guides   
~~Media t~~ype   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string 404 An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[ 

\]   
}   
"string" 

file:///Users/licaris/Downloads/account\_access.html 26/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
Headers:   
**Schwab**   
**Logo Developer Portal**   
Name Description Type 

Home   
Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error   
API Products 

User Guides   
~~Media t~~ype   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string POST/accounts/{accountNumber}/previewOrder   
Preview order for a specific account. 

Preview an order for a specific account. 

Parameters 

Try it out 

Name Description 

accountNumber \*   
string   
(path) 

Request body application/json 

The Order Object.   
The encrypted ID of the account accountNumber 

Example Value 

Schema 

{ 

"orderId": 0, 

"orderStrategy": { 

"accountNumber": "string", 

"advancedOrderType": "NONE", 

"closeTime": "2026-02-27T01:56:40.482Z", 

file:///Users/licaris/Downloads/account\_access.html 27/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal "enteredTime": "2026-02-27T01:56:40.482Z",   
Developer Portal   
**Charles**   
"orderBalance": {   
**Schwab**   
**Logo Developer Portal**   
"orderValue": 0, 

"projectedAvailableFund": 0,   
Home   
"projectedBuyingPower": 0,   
API Products   
~~"pr~~ojectedCommission": 0   
User Guides   
~~},~~ 

"orderStrategyType": "SINGLE", 

"orderVersion": 0, 

"session": "NORMAL", 

"status": "AWAITING\_PARENT\_ORDER", 

"allOrNone": true, 

"discretionary": true, 

"duration": "DAY", 

"filledQuantity": 0, 

"orderType": "MARKET", 

"orderValue": 0, 

"price": 0, 

"quantity": 0, 

"remainingQuantity": 0, 

"sellNonMarginableFirst": true, 

"settlementInstruction": "REGULAR", 

"strategy": "NONE", 

"amountIndicator": "DOLLARS", 

"orderLegs": \[ 

{ 

"askPrice": 0, 

"bidPrice": 0, 

"lastPrice": 0, 

"markPrice": 0, 

"projectedCommission": 0, 

"quantity": 0, 

"finalSymbol": "string", 

"legId": 0, 

"assetType": "EQUITY", 

"instruction": "BUY" 

} 

\] 

}, 

"orderValidationResult": { 

"alerts": \[ 

{ 

"validationRuleName": "string", 

"message": "string", 

"activityMessage": "string", 

"originalSeverity": "ACCEPT", 

"overrideName": "string", 

"overrideSeverity": "ACCEPT" 

} 

\], 

"accepts": \[ 

{ 

"validationRuleName": "string", 

"message": "string", 

"activityMessage": "string", 

"originalSeverity": "ACCEPT", 

"overrideName": "string", 

"overrideSeverity": "ACCEPT" 

} 

\], 

"rejects": \[ 

file:///Users/licaris/Downloads/account\_access.html 28/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal {   
Developer Portal   
**Charles Schwab**   
"validationRuleName": "string", 

**Logo Developer Portal**   
"message": "string", 

"activityMessage": "string",   
Home   
"originalSeverity": "ACCEPT",   
API Products   
~~"~~overrideName": "string",   
User Guides   
~~"~~overrideSeverity": "ACCEPT" 

} 

\], 

"reviews": \[ 

{ 

"validationRuleName": "string", 

"message": "string", 

"activityMessage": "string", 

"originalSeverity": "ACCEPT", 

"overrideName": "string", 

"overrideSeverity": "ACCEPT" 

} 

\], 

"warns": \[ 

{ 

"validationRuleName": "string", 

"message": "string", 

"activityMessage": "string", 

"originalSeverity": "ACCEPT", 

"overrideName": "string", 

"overrideSeverity": "ACCEPT" 

} 

\] 

}, 

"commissionAndFee": { 

"commission": { 

"commissionLegs": \[ 

{ 

"commissionValues": \[ 

{ 

"value": 0, 

"type": "COMMISSION" 

} 

\] 

} 

\] 

}, 

"fee": { 

"feeLegs": \[ 

{ 

"feeValues": \[ 

{ 

"value": 0, 

"type": "COMMISSION" 

} 

\] 

} 

\] 

}, 

"trueCommission": { 

"commissionLegs": \[ 

{ 

"commissionValues": \[ 

{ 

"value": 0, 

file:///Users/licaris/Downloads/account\_access.html 29/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
"type": "COMMISSION" }   
**Logo Developer Portal**   
\] 

}   
Home   
\]   
API Products   
~~}~~   
User Guides   
~~}~~ 

} 

Responses 

Code Description Links 

200 An order object, matching the input parameters 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

{   
"orderId": 0,   
"orderStrategy": {   
"accountNumber": "string",   
"advancedOrderType": "NONE",   
"closeTime": "2026-02-27T01:56:40.484Z", "enteredTime": "2026-02-27T01:56:40.484Z", "orderBalance": {   
"orderValue": 0,   
"projectedAvailableFund": 0,   
"projectedBuyingPower": 0,   
"projectedCommission": 0   
},   
"orderStrategyType": "SINGLE",   
"orderVersion": 0,   
"session": "NORMAL",   
"status": "AWAITING\_PARENT\_ORDER",   
"allOrNone": true,   
"discretionary": true,   
"duration": "DAY",   
"filledQuantity": 0,   
"orderType": "MARKET",   
"orderValue": 0,   
"price": 0,   
"quantity": 0,   
"remainingQuantity": 0,   
"sellNonMarginableFirst": true,   
"settlementInstruction": "REGULAR", "strategy": "NONE",   
"amountIndicator": "DOLLARS",   
"orderLegs": \[   
{ 

}   
\]   
},   
"askPrice": 0,   
"bidPrice": 0,   
"lastPrice": 0,   
"markPrice": 0,   
"projectedCommission": 0, "quantity": 0,   
"finalSymbol": "string", "legId": 0,   
"assetType": "EQUITY", "instruction": "BUY" 

"orderValidationResult": { "alerts": \[   
{ 

}   
\],   
"validationRuleName": "string", "message": "string",   
"activityMessage": "string", "originalSeverity": "ACCEPT", "overrideName": "string", "overrideSeverity": "ACCEPT" 

"accepts": \[   
{ 

}   
\],   
"validationRuleName": "string", "message": "string",   
"activityMessage": "string", "originalSeverity": "ACCEPT", "overrideName": "string", "overrideSeverity": "ACCEPT" 

"rejects": \[ 

file:///Users/licaris/Downloads/account\_access.html 30/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
{   
"validationRuleName": "string",   
**Logo Developer Portal**   
"message": "string",   
"activityMessage": "string", 

Home 

API Products   
}   
\],   
User Guides   
"originalSeverity": "ACCEPT", "overrideName": "string", "overrideSeverity": "ACCEPT" 

"reviews": \[   
{ 

}   
\],   
"validationRuleName": "string", "message": "string",   
"activityMessage": "string", "originalSeverity": "ACCEPT", "overrideName": "string", "overrideSeverity": "ACCEPT" 

"warns": \[   
{ 

}   
\]   
},   
"validationRuleName": "string", "message": "string",   
"activityMessage": "string", "originalSeverity": "ACCEPT", "overrideName": "string", "overrideSeverity": "ACCEPT" 

"commissionAndFee": { "commission": {   
"commissionLegs": \[   
{   
"commissionValues": \[ 

}   
\]   
},   
{ 

}   
\] 

"value": 0,   
"type": "COMMISSION" 

"fee": {   
"feeLegs": \[   
{   
"feeValues": \[ 

}   
\]   
},   
{ 

}   
\] 

"value": 0,   
"type": "COMMISSION" 

"trueCommission": { "commissionLegs": \[   
{   
"commissionValues": \[ 

}   
}   
}   
}   
\]   
{ 

}   
\]   
"value": 0,   
"type": "COMMISSION" 

Headers: 

Name Description Type 

Schwab-Client-CorrelId Correlation Id. Auto generated string 400 An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[ 

\]   
}   
"string" 

Headers: 

file:///Users/licaris/Downloads/account\_access.html 31/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string **Logo Developer Portal** 

Home   
An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application   
API Products   
~~Media ty~~pe   
User Guides   
~~applica~~tion/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 500 An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

No   
links 

{   
"message": "string", "errors": \[ 

\]   
}   
"string" 

Headers: 

file:///Users/licaris/Downloads/account\_access.html 32/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string **Logo Developer Portal** 

Home   
An error message indicating server has a temporary problem responding Media type   
API Products   
~~applicat~~ion/json   
User Guides   
Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

Transactions 

GET/accounts/{accountNumber}/transactions Get all transactions information for a specific account. 

All transactions for a specific account. Maximum number of transactions in response is 3000\. Maximum date range is 1 year. Parameters 

Try it out 

Name Description 

The encrypted ID of the account   
accountNumber \*   
string   
(path) 

startDate \* string   
(query) 

endDate \* string   
(query) 

symbol   
string   
(query) 

types \*   
string   
(query) 

Responses   
accountNumber 

Specifies that no transactions entered before this time should be returned. Valid ISO-8601 formats are : yyyy-MM-dd'T'HH:mm:ss.SSSZ . Example start date is '2024-03-28T21:10:42.000Z'. The 'endDate' must also be set. 

startDate 

Specifies that no transactions entered after this time should be returned.Valid ISO-8601 formats are : yyyy-MM-dd'T'HH:mm:ss.SSSZ. Example start date is '2024-05-10T21:10:42.000Z'. The 'startDate' must also be set. 

endDate 

It filters all the transaction activities based on the symbol specified. NOTE: If there is any special character in the symbol, please send th encoded value. 

symbol 

Specifies that only transactions of this status should be returned. 

Available values : TRADE, RECEIVE\_AND\_DELIVER, DIVIDEND\_OR\_INTEREST, ACH\_RECEIPT, ACH\_DISBURSEMENT, CASH\_RECEIPT, CASH\_DISBURSEMENT, ELECTRONIC\_FUND, WIRE\_OUT, WIRE\_IN, JOURNAL, MEMORANDUM, MARGIN\_CALL, MONEY\_MARKET, SMA\_ADJUSTMENT 

TRADE 

Code Description Links 

200 A List of orders for the account, matching the provided input parameters 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

\[   
{   
"activityId": 0,   
"time": "2026-02-27T01:56:40.490Z", "user": { 

file:///Users/licaris/Downloads/account\_access.html 33/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"cdDomainId": "string", "login": "string",   
**Logo Developer Portal**   
"type": "ADVISOR\_USER",   
"userId": 0, 

Home   
"systemUserName": "string", "firstName": "string", "lastName": "string",   
API Products   
"brokerRepCode": "string"   
},   
User Guides   
"description": "string",   
"accountNumber": "string",   
"type": "TRADE",   
"status": "VALID",   
"subAccount": "CASH",   
"tradeDate": "2026-02-27T01:56:40.490Z",   
"settlementDate": "2026-02-27T01:56:40.490Z", "positionId": 0,   
"orderId": 0,   
"netAmount": 0,   
"activityType": "ACTIVITY\_CORRECTION",   
"transferItems": \[ 

}   
\]   
{ 

}   
\]   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string", "instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE" },   
"amount": 0,   
"cost": 0,   
"price": 0,   
"feeType": "COMMISSION", "positionEffect": "OPENING" 

Headers: 

Name Description Type Schwab-Client-CorrelId Correlation Id. Auto generated string An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

file:///Users/licaris/Downloads/account\_access.html 34/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
An error message indicating the caller is forbidden from accessing this service   
**Schwab**   
**Logo Developer Portal**   
Media type   
application/json   
Home 

Example Value   
API Products   
Schema   
User Guides 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

file:///Users/licaris/Downloads/account\_access.html 35/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal GET/accounts/{accountNumber}/transactions/{transactionId}   
Developer Portal   
Get specific transaction information for a specific account   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Get specific transaction information for a specific account   
Home 

API Products   
Parameters   
User Guides 

Try it out 

Name Description 

accountNumber \*   
string   
(path) 

transactionId \* integer($int64) (path) 

Responses   
The encrypted ID of the account 

accountNumber 

The ID of the transaction being retrieved. transactionId 

Code Description Links A List of orders for the account, matching the provided input parameters 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema 

\[ 

200   
{   
"activityId": 0,   
"time": "2026-02-27T01:56:40.493Z",   
"user": {   
"cdDomainId": "string",   
"login": "string",   
"type": "ADVISOR\_USER",   
"userId": 0,   
"systemUserName": "string",   
"firstName": "string",   
"lastName": "string",   
"brokerRepCode": "string"   
},   
"description": "string",   
"accountNumber": "string",   
"type": "TRADE",   
"status": "VALID",   
"subAccount": "CASH",   
"tradeDate": "2026-02-27T01:56:40.493Z", "settlementDate": "2026-02-27T01:56:40.493Z", "positionId": 0,   
"orderId": 0,   
"netAmount": 0,   
"activityType": "ACTIVITY\_CORRECTION", "transferItems": \[ 

No   
links 

}   
\]   
{ 

}   
\]   
"instrument": {   
"cusip": "string",   
"symbol": "string",   
"description": "string", "instrumentId": 0,   
"netChange": 0,   
"type": "SWEEP\_VEHICLE" },   
"amount": 0,   
"cost": 0,   
"price": 0,   
"feeType": "COMMISSION", "positionEffect": "OPENING" 

Headers: 

Name Description Type 

Schwab-Client-CorrelId Correlation Id. Auto generated string 400 An error message indicating the validation problem with the request. 

Media type   
application/json 

No   
links 

file:///Users/licaris/Downloads/account\_access.html 36/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab** 

Example Value Schema   
**Logo Developer Portal** 

Home   
{ 

"message": "string", "errors": \[   
API Products   
"string"   
User Guides   
}   
\] 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

{   
404   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 500 An error message indicating there was an unexpected server error 

Media type   
application/json 

No   
links 

file:///Users/licaris/Downloads/account\_access.html 37/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab** 

Example Value Schema   
**Logo Developer Portal** 

Home   
{ 

"message": "string", "errors": \[   
API Products   
"string"   
User Guides   
}   
\] 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

UserPreference 

GET/userPreference Get user preference information for the logged in user. 

Get user preference information for the logged in user. 

Parameters 

Try it out 

No parameters 

Responses 

Code Description Links 

200 List of user preference values. 

Media type   
application/json   
Controls Accept header. 

Example Value   
Schema   
No   
links 

\[   
{   
"accounts": \[   
{ 

}   
\],   
"accountNumber": "string", "primaryAccount": false, "type": "string",   
"nickName": "string", "accountColor": "string", "displayAcctId": "string", "autoPositionEffect": false 

"streamerInfo": \[   
{   
"streamerSocketUrl": "string", "schwabClientCustomerId": "string", 

file:///Users/licaris/Downloads/account\_access.html 38/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles Schwab**   
"schwabClientCorrelId": "string", "schwabClientChannel": "string",   
**Logo Developer Portal** 

Home   
}   
\],   
"schwabClientFunctionId": "string" 

"offers": \[ {   
API Products   
"level2Permissions": false,   
User Guides   
}   
"mktDataPermission": "string" 

}   
\]   
\] 

An error message indicating the validation problem with the request. 

Media type   
application/json 

Example Value   
Schema 

{   
400   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

An error message indicating either authorization token is invalid or there are no accounts the caller is allowed to view or use for trading that are registered with the provided third party application 

Media type   
application/json 

Example Value   
Schema 

401   
{   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating the caller is forbidden from accessing this service 

Media type   
application/json 

Example Value   
Schema 

{   
403   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type Schwab-Client-CorrelID Correlation Id. Auto generated string 404 An error message indicating the resource is not found 

Media type   
application/json 

Example Value   
Schema 

No   
links 

file:///Users/licaris/Downloads/account\_access.html 39/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal Code Description Links Developer Portal   
**Charles**   
{   
**Schwab**   
"message": "string",   
**Logo Developer Portal**   
"errors": \[ 

Home   
\]   
}   
"string" 

API Products 

User Guides   
~~Heade~~rs: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating there was an unexpected server error 

Media type   
application/json 

Example Value   
Schema 

{   
500   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string An error message indicating server has a temporary problem responding 

Media type   
application/json 

Example Value   
Schema 

{   
503   
"message": "string", "errors": \[ 

No   
links 

\]   
}   
"string" 

Headers: 

Name Description Type 

Schwab-Client-CorrelID Correlation Id. Auto generated string 

Schemas 

AccountNumberHash { 

accountNumber string 

hashValue string 

} 

session stringEnum: 

\[ NORMAL, AM, PM, SEAMLESS \] 

duration stringEnum: 

\[ DAY, GOOD\_TILL\_CANCEL, FILL\_OR\_KILL, IMMEDIATE\_OR\_CANCEL, END\_OF\_WEEK, END\_OF\_MONTH, NEXT\_END\_OF\_MONTH, UNKNOWN \] 

orderType stringEnum: 

\[ MARKET, LIMIT, STOP, STOP\_LIMIT, TRAILING\_STOP, CABINET, NON\_MARKETABLE, MARKET\_ON\_CLOSE, EXERCISE, TRAILING\_STOP\_LIMIT, NET\_DEBIT, NET\_CREDIT, NET\_ZERO, LIMIT\_ON\_CLOSE, UNKNOWN \] 

orderTypeRequest string 

Same as orderType, but does not have UNKNOWN since this type is not allowed as an input 

Enum: 

\[ MARKET, LIMIT, STOP, STOP\_LIMIT, TRAILING\_STOP, CABINET, NON\_MARKETABLE, MARKET\_ON\_CLOSE, EXERCISE, TRAILING\_STOP\_LIMIT, NET\_DEBIT, NET\_CREDIT, NET\_ZERO, LIMIT\_ON\_CLOSE \] 

file:///Users/licaris/Downloads/account\_access.html 40/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal complexOrderStrategyType stringEnum:   
Developer Portal   
**Charles**   
\[ NONE, COVERED, VERTICAL, BACK\_RATIO, CALENDAR, DIAGONAL, STRADDLE, STRANGLE, COLLAR\_SYNTHETIC, BUTTERFLY,   
**Schwab**   
**Logo Developer Portal**   
CONDOR, IRON\_CONDOR, VERTICAL\_ROLL, COLLAR\_WITH\_STOCK, DOUBLE\_DIAGONAL, UNBALANCED\_BUTTERFLY, UNBALANCED\_CONDOR, UNBALANCED\_IRON\_CONDOR, UNBALANCED\_VERTICAL\_ROLL, MUTUAL\_FUND\_SWAP, CUSTOM \] Home   
~~requ~~estedDestination stringEnum:   
API Products   
~~\[ INET, ECN~~\_ARCA, CBOE, AMEX, PHLX, ISE, BOX, NYSE, NASDAQ, BATS, C2, AUTO \]   
User Guides   
~~stopPriceLinkB~~asis stringEnum: 

\[ MANUAL, BASE, TRIGGER, LAST, BID, ASK, ASK\_BID, MARK, AVERAGE \] 

stopPriceLinkType stringEnum: 

\[ VALUE, PERCENT, TICK \] 

stopPriceOffset number($double) 

stopType stringEnum: 

\[ STANDARD, BID, ASK, LAST, MARK \] 

priceLinkBasis stringEnum: 

\[ MANUAL, BASE, TRIGGER, LAST, BID, ASK, ASK\_BID, MARK, AVERAGE \] 

priceLinkType stringEnum: 

\[ VALUE, PERCENT, TICK \] 

taxLotMethod stringEnum: 

\[ FIFO, LIFO, HIGH\_COST, LOW\_COST, AVERAGE\_COST, SPECIFIC\_LOT, LOSS\_HARVESTER \] 

specialInstruction stringEnum: 

\[ ALL\_OR\_NONE, DO\_NOT\_REDUCE, ALL\_OR\_NONE\_DO\_NOT\_REDUCE \] 

orderStrategyType stringEnum: 

\[ SINGLE, CANCEL, RECALL, PAIR, FLATTEN, TWO\_DAY\_SWAP, BLAST\_ALL, OCO, TRIGGER \] 

status stringEnum: 

\[ AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME, PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN \] amountIndicator stringEnum: 

\[ DOLLARS, SHARES, ALL\_SHARES, PERCENTAGE, UNKNOWN \] 

settlementInstruction stringEnum: 

\[ REGULAR, CASH, NEXT\_DAY, UNKNOWN \] 

OrderStrategy { 

accountNumber string 

advancedOrderTypestringEnum:   
\[ NONE, OTO, OCO, OTOCO, OT2OCO, OT3OCO, BLAST\_ALL, OTA, PAIR \] 

closeTime string($date-time) 

enteredTime string($date-time) 

\#/components/schemas/OrderBalanceOrderBalance { 

orderValue number($double) 

orderBalance   
projectedAvailableFund number($double) projectedBuyingPower number($double) projectedCommission number($double) } 

orderStrategyType orderStrategyTypestringEnum:   
\[ SINGLE, CANCEL, RECALL, PAIR, FLATTEN, TWO\_DAY\_SWAP, BLAST\_ALL, OCO, TRIGGER \] orderVersion number 

sessionsessionstringEnum:   
\[ NORMAL, AM, PM, SEAMLESS \] 

apiOrderStatusstringEnum: 

status   
\[ AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME, PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN \] 

allOrNone boolean discretionary boolean 

duration   
durationstringEnum:   
\[ DAY, GOOD\_TILL\_CANCEL, FILL\_OR\_KILL, IMMEDIATE\_OR\_CANCEL, END\_OF\_WEEK, END\_OF\_MONTH, NEXT\_END\_OF\_MONTH, UNKNOWN \] 

filledQuantity number($double) 

orderType   
orderTypestringEnum:   
\[ MARKET, LIMIT, STOP, STOP\_LIMIT, TRAILING\_STOP, CABINET, NON\_MARKETABLE, MARKET\_ON\_CLOSE, EXERCISE, TRAILING\_STOP\_LIMIT, NET\_DEBIT, NET\_CREDIT, NET\_ZERO, LIMIT\_ON\_CLOSE, UNKNOWN \] 

orderValue number($double) 

price number($double) 

quantity number($double) 

file:///Users/licaris/Downloads/account\_access.html 41/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal remainingQuantity number($double)   
Developer Portal   
**Charles**   
sellNonMarginableFirst boolean   
**Schwab**   
settlementInstructionsettlementInstructionstringEnum:   
**Logo Developer Portal**   
\[ REGULAR, CASH, NEXT\_DAY, UNKNOWN \] 

Home 

API Products   
strategy   
User Guides   
complexOrderStrategyTypestringEnum:   
\[ NONE, COVERED, VERTICAL, BACK\_RATIO, CALENDAR, DIAGONAL, STRADDLE, STRANGLE, COLLAR\_SYNTHETIC, BUTTERFLY, CONDOR, IRON\_CONDOR, VERTICAL\_ROLL, COLLAR\_WITH\_STOCK, DOUBLE\_DIAGONAL, UNBALANCED\_BUTTERFLY, UNBALANCED\_CONDOR, UNBALANCED\_IRON\_CONDOR, UNBALANCED\_VERTICAL\_ROLL, MUTUAL\_FUND\_SWAP, CUSTOM \] 

amountIndicatoramountIndicatorstringEnum:   
\[ DOLLARS, SHARES, ALL\_SHARES, PERCENTAGE, UNKNOWN \] 

\[   
xml: OrderedMap { "name": "orderLeg", "wrapped": true }   
\#/components/schemas/OrderLegOrderLeg { 

askPrice number($double) 

bidPrice number($double) 

lastPrice number($double) 

markPrice number($double) 

projectedCommission number($double) 

orderLegs   
quantity number($double) finalSymbol string 

legId number($long) 

assetType 

instruction 

}\] 

} 

OrderLeg { 

askPrice number($double) bidPrice number($double) lastPrice number($double) markPrice number($double) projectedCommission number($double) quantity number($double) finalSymbol string 

legId number($long)   
assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] instructionstringEnum:   
\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

assetType 

instruction 

} 

OrderBalance {   
assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

instructionstringEnum:   
\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

orderValue number($double) 

projectedAvailableFund number($double) 

projectedBuyingPower number($double) 

projectedCommission number($double) 

} 

OrderValidationResult { 

\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
message string 

activityMessage string 

alerts   
originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \]   
}\] 

accepts \[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { 

validationRuleName string 

message string 

activityMessage string 

originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

file:///Users/licaris/Downloads/account\_access.html 42/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal overrideName string   
Developer Portal   
**Charles**   
overrideSeverityAPIRuleActionstringEnum:   
**Schwab**   
**Logo Developer Portal** }\]   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

Home   
\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
API Products   
~~mes~~sage string   
User Guides   
~~act~~ivityMessage string 

rejects   
originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \]   
}\] 

\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
message string 

activityMessage string 

reviews   
originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

warns }   
}\] 

\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
message string 

activityMessage string 

originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \]   
}\] 

OrderValidationDetail { 

validationRuleName string 

message string 

activityMessage string 

originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

} 

APIRuleAction stringEnum: 

\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

CommissionAndFee { 

\#/components/schemas/CommissionCommission { 

\[ \#/components/schemas/CommissionLegCommissionLeg { 

\[ \#/components/schemas/CommissionValueCommissionValue { 

value number($double) 

FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE,   
ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE,   
commission   
commissionLegs   
commissionValues 

type 

}\]   
}\]   
}   
FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE,   
FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

fee \#/components/schemas/FeesFees { 

feeLegs \[ \#/components/schemas/FeeLegFeeLeg { 

feeValues \[ \#/components/schemas/FeeValueFeeValue { 

value number($double) 

file:///Users/licaris/Downloads/account\_access.html 43/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides   
}\]   
} 

type }\]   
FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

trueCommission   
\#/components/schemas/CommissionCommission { 

\[ \#/components/schemas/CommissionLegCommissionLeg { 

\[ \#/components/schemas/CommissionValueCommissionValue { 

value number($double) 

FeeTypestringEnum: 

\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE,   
ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE,   
FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE,   
commissionLegs   
commissionValues   
type 

}\]   
}\]   
} 

} 

Commission { 

FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE,   
FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

commissionLegs   
\[ \#/components/schemas/CommissionLegCommissionLeg { 

\[ \#/components/schemas/CommissionValueCommissionValue { 

value number($double) 

FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE,   
ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE,   
commissionValues   
type 

}\]   
}\] 

} 

CommissionLeg {   
FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

\[ \#/components/schemas/CommissionValueCommissionValue { value number($double)   
FeeTypestringEnum: 

commissionValues type 

}\] 

} 

CommissionValue { 

value number($double)   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, type   
FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] } 

Fees { 

feeLegs \[ \#/components/schemas/FeeLegFeeLeg { \[ \#/components/schemas/FeeValueFeeValue { 

value number($double) 

FeeTypestringEnum: 

feeValues   
type 

}\]   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

file:///Users/licaris/Downloads/account\_access.html 44/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal }\]   
Developer Portal   
}   
**Charles Schwab**   
FeeLeg {   
**Logo Developer Portal**   
\[ \#/components/schemas/FeeValueFeeValue {   
Home   
value number($double)   
API Products User Guides ~~feeValues~~   
type 

}\] 

} 

FeeValue {   
FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE,   
LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

value number($double) FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, type   
FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] } 

FeeType stringEnum: 

\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE, FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

Account { 

securitiesAccount \#/components/schemas/SecuritiesAccountSecuritiesAccount { 

oneOf \-\>   
\#/components/schemas/MarginAccountMarginAccount { 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

isClosingOnlyRestrictedboolean   
default: false 

pfcbFlagboolean   
default: false 

positions \[ \#/components/schemas/PositionPosition { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEq stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY 

}\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

cusip string 

symbol string 

description string 

file:///Users/licaris/Downloads/account\_access.html 45/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal**   
instrumentId integer($int64) 

netChange number($double)   
}   
\#/components/schemas/AccountFixedIncomeAccountFixedIncom 

Home 

API Products 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

User Guides 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double)   
}   
\#/components/schemas/AccountMutualFundAccountMutualFund 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUT FIXED\_INCOME, CURRENCY, COL 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optio   
\#/components/schemas/AccountAPIOpti   
{   
symbol string($int64) 

deliverableUnits number($doubl 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, E assetTypestring \[ EQUITY, MU   
FOREX, INDEX, FIXED\_INCOME COLLECTIVE\_IN 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, U 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double)   
}\] 

file:///Users/licaris/Downloads/account\_access.html 46/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides 

initialBalances 

currentBalances   
\#/components/schemas/MarginInitialBalanceMarginInitialBalance { accruedInterest number($double) availableFundsNonMarginableTrade number($double) bondValue number($double) buyingPower number($double) cashBalance number($double) cashAvailableForTrading number($double) cashReceipts number($double) dayTradingBuyingPower number($double) dayTradingBuyingPowerCall number($double) dayTradingEquityCall number($double) equity number($double) equityPercentage number($double) liquidationValue number($double) longMarginValue number($double) longOptionMarketValue number($double) longStockValue number($double) maintenanceCall number($double) maintenanceRequirement number($double) margin number($double) marginEquity number($double) moneyMarketFund number($double) mutualFundValue number($double) regTCall number($double) shortMarginValue number($double) shortOptionMarketValue number($double) shortStockValue number($double) totalCash number($double) isInCall number($double) unsettledCash number($double) pendingDeposits number($double) marginBalance number($double) shortBalance number($double) accountValue number($double) } 

\#/components/schemas/MarginBalanceMarginBalance { availableFunds number($double) availableFundsNonMarginableTrade number($double) buyingPower number($double) buyingPowerNonMarginableTrade number($double) dayTradingBuyingPower number($double) dayTradingBuyingPowerCall number($double) equity number($double) equityPercentage number($double) longMarginValue number($double) maintenanceCall number($double) maintenanceRequirement number($double) marginBalance number($double) regTCall number($double) shortBalance number($double) shortMarginValue number($double) sma number($double) isInCall number($double) stockBuyingPower number($double) optionBuyingPower number($double) } 

projectedBalances \#/components/schemas/MarginBalanceMarginBalance { 

availableFunds number($double) 

availableFundsNonMarginableTrade number($double) 

buyingPower number($double) 

buyingPowerNonMarginableTrade number($double) 

dayTradingBuyingPower number($double) 

dayTradingBuyingPowerCall number($double) 

equity number($double) 

equityPercentage number($double) 

longMarginValue number($double) 

file:///Users/licaris/Downloads/account\_access.html 47/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides   
maintenanceCall number($double) maintenanceRequirement number($double) marginBalance number($double) regTCall number($double) shortBalance number($double) shortMarginValue number($double) sma number($double) isInCall number($double) stockBuyingPower number($double) optionBuyingPower number($double) } 

}\#/components/schemas/CashAccountCashAccount { 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

isClosingOnlyRestrictedboolean   
default: false 

pfcbFlagboolean   
default: false 

positions \[ \#/components/schemas/PositionPosition { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEq stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY 

}\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double)   
}   
\#/components/schemas/AccountFixedIncomeAccountFixedIncom 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double)   
} 

file:///Users/licaris/Downloads/account\_access.html 48/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
\#/components/schemas/AccountMutualFundAccountMutualFund stringEnum:   
**Schwab**   
**Logo Developer Portal** 

assetType\* 

\[ EQUITY, OPTION, INDEX, MUTUAL\_ FIXED\_INCOME, CURRENCY, COLLECT 

Home 

API Products User Guides   
cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUT FIXED\_INCOME, CURRENCY, COL 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optio   
\#/components/schemas/AccountAPIOpti   
{   
symbol string($int64) 

deliverableUnits number($doubl 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, E assetTypestring \[ EQUITY, MU   
FOREX, INDEX, FIXED\_INCOME COLLECTIVE\_IN 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, U 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double)   
}\] 

initialBalances \#/components/schemas/CashInitialBalanceCashInitialBalance { 

accruedInterest number($double) 

cashAvailableForTrading number($double) 

cashAvailableForWithdrawal number($double) 

cashBalance number($double) 

bondValue number($double) 

cashReceipts number($double) 

liquidationValue number($double) 

longOptionMarketValue number($double) 

longStockValue number($double) 

moneyMarketFund number($double) 

mutualFundValue number($double) 

shortOptionMarketValue number($double) 

shortStockValue number($double) 

isInCall number($double) 

unsettledCash number($double) 

file:///Users/licaris/Downloads/account\_access.html 49/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides 

currentBalances 

projectedBalances 

}   
} 

} 

DateParam { 

string 

date   
Valid ISO-8601 format is :   
yyyy-MM-dd'T'HH:mm:ss.SSSZ 

} 

Order { 

sessionsessionstringEnum:   
cashDebitCallValue number($double) pendingDeposits number($double) accountValue number($double) } 

\#/components/schemas/CashBalanceCashBalance { cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashCall number($double) longNonMarginableMarketValue number($double) totalCash number($double) cashDebitCallValue number($double) unsettledCash number($double) } 

\#/components/schemas/CashBalanceCashBalance { cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashCall number($double) longNonMarginableMarketValue number($double) totalCash number($double) cashDebitCallValue number($double) unsettledCash number($double) } 

duration orderType   
\[ NORMAL, AM, PM, SEAMLESS \] 

durationstringEnum:   
\[ DAY, GOOD\_TILL\_CANCEL, FILL\_OR\_KILL, IMMEDIATE\_OR\_CANCEL, END\_OF\_WEEK, END\_OF\_MONTH, NEXT\_END\_OF\_MONTH, UNKNOWN \] 

orderTypestringEnum:   
\[ MARKET, LIMIT, STOP, STOP\_LIMIT, TRAILING\_STOP, CABINET, NON\_MARKETABLE, MARKET\_ON\_CLOSE, EXERCISE, TRAILING\_STOP\_LIMIT, NET\_DEBIT, NET\_CREDIT, NET\_ZERO, LIMIT\_ON\_CLOSE, UNKNOWN \] 

cancelTime string($date-time) 

complexOrderStrategyTypestringEnum: 

complexOrderStrategyType   
\[ NONE, COVERED, VERTICAL, BACK\_RATIO, CALENDAR, DIAGONAL, STRADDLE, STRANGLE,   
COLLAR\_SYNTHETIC, BUTTERFLY, CONDOR, IRON\_CONDOR, VERTICAL\_ROLL, COLLAR\_WITH\_STOCK,   
DOUBLE\_DIAGONAL, UNBALANCED\_BUTTERFLY, UNBALANCED\_CONDOR, UNBALANCED\_IRON\_CONDOR,   
UNBALANCED\_VERTICAL\_ROLL, MUTUAL\_FUND\_SWAP, CUSTOM \] 

quantity number($double) 

filledQuantity number($double) 

remainingQuantity number($double) 

requestedDestinationrequestedDestinationstringEnum:   
\[ INET, ECN\_ARCA, CBOE, AMEX, PHLX, ISE, BOX, NYSE, NASDAQ, BATS, C2, AUTO \] 

destinationLinkName string 

releaseTime string($date-time) 

stopPrice number($double) 

stopPriceLinkBasisstopPriceLinkBasisstringEnum:   
\[ MANUAL, BASE, TRIGGER, LAST, BID, ASK, ASK\_BID, MARK, AVERAGE \] 

stopPriceLinkTypestopPriceLinkTypestringEnum:   
\[ VALUE, PERCENT, TICK \] 

stopPriceOffset number($double) 

stopTypestopTypestringEnum:   
\[ STANDARD, BID, ASK, LAST, MARK \] 

priceLinkBasis priceLinkBasisstringEnum:   
\[ MANUAL, BASE, TRIGGER, LAST, BID, ASK, ASK\_BID, MARK, AVERAGE \] 

priceLinkType priceLinkTypestringEnum:   
\[ VALUE, PERCENT, TICK \] 

price number($double) 

taxLotMethodtaxLotMethodstringEnum:   
\[ FIFO, LIFO, HIGH\_COST, LOW\_COST, AVERAGE\_COST, SPECIFIC\_LOT, LOSS\_HARVESTER \] 

orderLegCollection \[   
xml: OrderedMap { "name": "orderLegCollection", "wrapped": true } 

file:///Users/licaris/Downloads/account\_access.html 50/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
\#/components/schemas/OrderLegCollectionOrderLegCollection { stringEnum:   
**Schwab**   
orderLegType 

\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME,   
**Logo Developer Portal** 

CURRENCY, COLLECTIVE\_INVESTMENT \] 

Home 

API Products   
legId integer($int64) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

User Guides   
oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

optionDeliverables \[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped": true }   
\#/components/schemas/AccountAPIOptionDeliverableAccountAPIOptionDeliverable   
{   
symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
\[ USD, CAD, EUR, JPY \] 

assetType assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, 

file:///Users/licaris/Downloads/account\_access.html 51/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides 

instruction   
FOREX, INDEX, CASH\_EQUIVALENT,   
FIXED\_INCOME, PRODUCT, CURRENCY,   
COLLECTIVE\_INVESTMENT \]   
}\] 

putCallstringEnum: 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum: 

\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

instructionstringEnum:   
\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

positionEffectstringEnum:   
\[ OPENING, CLOSING, AUTOMATIC \] 

quantity number($double) 

quantityTypestringEnum:   
\[ ALL\_SHARES, DOLLARS, SHARES \] 

divCapGainsstringEnum:   
\[ REINVEST, PAYOUT \] 

toSymbol string   
}\] 

activationPrice number($double) 

specialInstructionspecialInstructionstringEnum:   
\[ ALL\_OR\_NONE, DO\_NOT\_REDUCE, ALL\_OR\_NONE\_DO\_NOT\_REDUCE \] 

orderStrategyType orderStrategyTypestringEnum:   
\[ SINGLE, CANCEL, RECALL, PAIR, FLATTEN, TWO\_DAY\_SWAP, BLAST\_ALL, OCO, TRIGGER \] orderId integer($int64) 

cancelable boolean   
default: false 

editable boolean   
default: false 

statusstringEnum: 

status   
\[ AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME, PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN \] 

enteredTime string($date-time) 

closeTime string($date-time) 

tag string 

accountNumber integer($int64) 

\[   
xml: OrderedMap { "name": "orderActivity", "wrapped": true }   
\#/components/schemas/OrderActivityOrderActivity { 

activityTypestringEnum:   
\[ EXECUTION, ORDER\_ACTION \] 

executionTypestringEnum:   
\[ FILL \] 

quantity number($double) 

orderRemainingQuantity number($double) 

orderActivityCollection 

executionLegs 

}\] 

\[   
\[   
xml: OrderedMap { "name": "executionLegs", "wrapped": true } \#/components/schemas/ExecutionLegExecutionLeg { 

legId integer($int64) 

price number($double) 

quantity number($double) 

mismarkedQuantity number($double) 

instrumentId integer($int64) 

time string($date-time)   
}\] 

replacingOrderCollection childOrderStrategies   
xml: OrderedMap { "name": "replacingOrder", "wrapped": true } {   
}\] 

\[   
xml: OrderedMap { "name": "childOrder", "wrapped": true } {   
}\] 

statusDescription string 

file:///Users/licaris/Downloads/account\_access.html 52/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal }   
Developer Portal   
**Charles**   
OrderRequest {   
**Schwab**   
**Logo Developer Portal**   
sessionsessionstringEnum:   
\[ NORMAL, AM, PM, SEAMLESS \] 

Home   
duration   
API Products User Guides 

orderType   
durationstringEnum:   
\[ DAY, GOOD\_TILL\_CANCEL, FILL\_OR\_KILL, IMMEDIATE\_OR\_CANCEL, END\_OF\_WEEK, END\_OF\_MONTH, NEXT\_END\_OF\_MONTH, UNKNOWN \] 

orderTypeRequeststring 

Same as orderType, but does not have UNKNOWN since this type is not allowed as an input 

Enum:   
\[ MARKET, LIMIT, STOP, STOP\_LIMIT, TRAILING\_STOP, CABINET, NON\_MARKETABLE, MARKET\_ON\_CLOSE, EXERCISE, TRAILING\_STOP\_LIMIT, NET\_DEBIT, NET\_CREDIT, NET\_ZERO, LIMIT\_ON\_CLOSE \] 

cancelTime string($date-time) 

complexOrderStrategyTypestringEnum: 

complexOrderStrategyType   
\[ NONE, COVERED, VERTICAL, BACK\_RATIO, CALENDAR, DIAGONAL, STRADDLE, STRANGLE,   
COLLAR\_SYNTHETIC, BUTTERFLY, CONDOR, IRON\_CONDOR, VERTICAL\_ROLL, COLLAR\_WITH\_STOCK, DOUBLE\_DIAGONAL, UNBALANCED\_BUTTERFLY, UNBALANCED\_CONDOR, UNBALANCED\_IRON\_CONDOR, UNBALANCED\_VERTICAL\_ROLL, MUTUAL\_FUND\_SWAP, CUSTOM \] 

quantity number($double) 

filledQuantity number($double) 

remainingQuantity number($double) 

destinationLinkName string 

releaseTime string($date-time) 

stopPrice number($double) 

stopPriceLinkBasisstopPriceLinkBasisstringEnum:   
\[ MANUAL, BASE, TRIGGER, LAST, BID, ASK, ASK\_BID, MARK, AVERAGE \] 

stopPriceLinkTypestopPriceLinkTypestringEnum:   
\[ VALUE, PERCENT, TICK \] 

stopPriceOffset number($double) 

stopTypestopTypestringEnum:   
\[ STANDARD, BID, ASK, LAST, MARK \] 

priceLinkBasis priceLinkBasisstringEnum:   
\[ MANUAL, BASE, TRIGGER, LAST, BID, ASK, ASK\_BID, MARK, AVERAGE \] 

priceLinkType priceLinkTypestringEnum:   
\[ VALUE, PERCENT, TICK \] 

price number($double) 

taxLotMethodtaxLotMethodstringEnum:   
\[ FIFO, LIFO, HIGH\_COST, LOW\_COST, AVERAGE\_COST, SPECIFIC\_LOT, LOSS\_HARVESTER \] 

orderLegCollection \[   
xml: OrderedMap { "name": "orderLegCollection", "wrapped": true }   
\#/components/schemas/OrderLegCollectionOrderLegCollection { 

stringEnum:   
orderLegType   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME,   
CURRENCY, COLLECTIVE\_INVESTMENT \] 

legId integer($int64) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double)   
} 

file:///Users/licaris/Downloads/account\_access.html 53/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
\#/components/schemas/AccountFixedIncomeAccountFixedIncome { stringEnum:   
**Schwab**   
**Logo Developer Portal** 

assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

Home 

API Products User Guides   
cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped": true } \#/components/schemas/AccountAPIOptionDeliverableAccountAPIOptionDeliverable { 

symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

instruction   
\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum: 

\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

instructionstringEnum:   
\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

positionEffectstringEnum:   
\[ OPENING, CLOSING, AUTOMATIC \] 

quantity number($double) 

quantityTypestringEnum:   
\[ ALL\_SHARES, DOLLARS, SHARES \] 

divCapGainsstringEnum:   
\[ REINVEST, PAYOUT \] 

toSymbol string   
}\] 

activationPrice number($double) 

specialInstructionspecialInstructionstringEnum:   
\[ ALL\_OR\_NONE, DO\_NOT\_REDUCE, ALL\_OR\_NONE\_DO\_NOT\_REDUCE \] 

orderStrategyType orderStrategyTypestringEnum:   
\[ SINGLE, CANCEL, RECALL, PAIR, FLATTEN, TWO\_DAY\_SWAP, BLAST\_ALL, OCO, TRIGGER \] 

file:///Users/licaris/Downloads/account\_access.html 54/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal orderId integer($int64)   
Developer Portal   
**Charles**   
cancelable boolean   
**Schwab** 

default: false   
**Logo Developer Portal** editable boolean   
Home 

API Products status   
User Guides   
default: false 

statusstringEnum:   
\[ AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME, PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN \] 

enteredTime string($date-time) 

closeTime string($date-time) 

accountNumber integer($int64) 

\[   
xml: OrderedMap { "name": "orderActivity", "wrapped": true }   
\#/components/schemas/OrderActivityOrderActivity { 

activityTypestringEnum:   
\[ EXECUTION, ORDER\_ACTION \] 

executionTypestringEnum:   
\[ FILL \] 

quantity number($double) 

orderRemainingQuantity number($double) 

orderActivityCollection 

executionLegs 

}\] 

\[   
\[   
xml: OrderedMap { "name": "executionLegs", "wrapped": true } \#/components/schemas/ExecutionLegExecutionLeg { 

legId integer($int64) 

price number($double) 

quantity number($double) 

mismarkedQuantity number($double) 

instrumentId integer($int64) 

time string($date-time)   
}\] 

replacingOrderCollection childOrderStrategies   
xml: OrderedMap { "name": "replacingOrder", "wrapped": true } {   
}\] 

\[   
xml: OrderedMap { "name": "childOrder", "wrapped": true } {   
}\] 

statusDescription string 

} 

PreviewOrder { 

orderId integer($int64) 

orderStrategy \#/components/schemas/OrderStrategyOrderStrategy { 

accountNumber string 

advancedOrderTypestringEnum:   
\[ NONE, OTO, OCO, OTOCO, OT2OCO, OT3OCO, BLAST\_ALL, OTA, PAIR \] 

closeTime string($date-time) 

enteredTime string($date-time) 

\#/components/schemas/OrderBalanceOrderBalance { 

orderValue number($double) 

orderBalance 

orderStrategyType   
projectedAvailableFund number($double) 

projectedBuyingPower number($double) 

projectedCommission number($double)   
} 

orderStrategyTypestringEnum:   
\[ SINGLE, CANCEL, RECALL, PAIR, FLATTEN, TWO\_DAY\_SWAP, BLAST\_ALL, OCO, TRIGGER \] 

orderVersion number 

sessionsessionstringEnum:   
\[ NORMAL, AM, PM, SEAMLESS \] 

apiOrderStatusstringEnum:   
\[ AWAITING\_PARENT\_ORDER, AWAITING\_CONDITION, AWAITING\_STOP\_CONDITION, 

status   
AWAITING\_MANUAL\_REVIEW, ACCEPTED, AWAITING\_UR\_OUT, PENDING\_ACTIVATION, QUEUED, WORKING, REJECTED, PENDING\_CANCEL, CANCELED, PENDING\_REPLACE, REPLACED, FILLED, EXPIRED, NEW, AWAITING\_RELEASE\_TIME,   
PENDING\_ACKNOWLEDGEMENT, PENDING\_RECALL, UNKNOWN \] 

allOrNone boolean 

discretionary boolean 

file:///Users/licaris/Downloads/account\_access.html 55/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
duration   
durationstringEnum:   
\[ DAY, GOOD\_TILL\_CANCEL, FILL\_OR\_KILL, IMMEDIATE\_OR\_CANCEL, END\_OF\_WEEK, END\_OF\_MONTH, NEXT\_END\_OF\_MONTH, UNKNOWN \]   
**Logo Developer Portal**   
filledQuantity number($double) 

Home 

API Products User Guides   
orderType   
orderTypestringEnum:   
\[ MARKET, LIMIT, STOP, STOP\_LIMIT, TRAILING\_STOP, CABINET, NON\_MARKETABLE, MARKET\_ON\_CLOSE, EXERCISE, TRAILING\_STOP\_LIMIT, NET\_DEBIT, NET\_CREDIT, NET\_ZERO, LIMIT\_ON\_CLOSE, UNKNOWN \] 

orderValue number($double) 

price number($double) 

quantity number($double) 

remainingQuantity number($double) 

sellNonMarginableFirst boolean 

settlementInstructionsettlementInstructionstringEnum:   
\[ REGULAR, CASH, NEXT\_DAY, UNKNOWN \] 

complexOrderStrategyTypestringEnum:   
\[ NONE, COVERED, VERTICAL, BACK\_RATIO, CALENDAR, DIAGONAL, STRADDLE, 

strategy   
STRANGLE, COLLAR\_SYNTHETIC, BUTTERFLY, CONDOR, IRON\_CONDOR, VERTICAL\_ROLL, COLLAR\_WITH\_STOCK, DOUBLE\_DIAGONAL,   
UNBALANCED\_BUTTERFLY, UNBALANCED\_CONDOR, UNBALANCED\_IRON\_CONDOR, UNBALANCED\_VERTICAL\_ROLL, MUTUAL\_FUND\_SWAP, CUSTOM \] 

amountIndicatoramountIndicatorstringEnum:   
\[ DOLLARS, SHARES, ALL\_SHARES, PERCENTAGE, UNKNOWN \] 

\[   
xml: OrderedMap { "name": "orderLeg", "wrapped": true }   
\#/components/schemas/OrderLegOrderLeg { 

askPrice number($double) 

bidPrice number($double) 

lastPrice number($double) 

markPrice number($double) 

projectedCommission number($double) 

quantity number($double) 

orderLegs   
finalSymbol string 

legId number($long) assetTypestringEnum: 

assetType 

instruction 

}\]   
}   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

instructionstringEnum:   
\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

orderValidationResult \#/components/schemas/OrderValidationResultOrderValidationResult { \[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { 

validationRuleName string 

message string 

activityMessage string 

alerts   
originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \]   
}\] 

\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
message string 

activityMessage string 

originalSeverityAPIRuleActionstringEnum:   
accepts   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \]   
}\] 

rejects \[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { 

validationRuleName string 

message string 

activityMessage string 

originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

file:///Users/licaris/Downloads/account\_access.html 56/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal**   
overrideName string 

overrideSeverityAPIRuleActionstringEnum: 

\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

Home 

API Products User Guides   
}\] 

\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
message string 

activityMessage string 

reviews   
originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

warns }   
}\] 

\[ \#/components/schemas/OrderValidationDetailOrderValidationDetail { validationRuleName string   
message string 

activityMessage string 

originalSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \] 

overrideName string 

overrideSeverityAPIRuleActionstringEnum:   
\[ ACCEPT, ALERT, REJECT, REVIEW, UNKNOWN \]   
}\] 

commissionAndFee \#/components/schemas/CommissionAndFeeCommissionAndFee { 

\#/components/schemas/CommissionCommission { 

\[ \#/components/schemas/CommissionLegCommissionLeg { 

\[ \#/components/schemas/CommissionValueCommissionValue { 

value number($double) 

FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE,   
CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT,   
FUTURES\_CLEARING\_FEE, 

commission   
commissionLegs 

commissionValues   
type 

}\]   
}\]   
} 

\#/components/schemas/FeesFees {   
FUTURES\_DESK\_OFFICE\_FEE,   
FUTURES\_EXCHANGE\_FEE,   
FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE,   
FUTURES\_TRANSACTION\_FEE,   
LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

fee   
feeLegs   
\[ \#/components/schemas/FeeLegFeeLeg { 

\[ \#/components/schemas/FeeValueFeeValue { 

value number($double) 

FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE, CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, FUTURES\_CLEARING\_FEE, FUTURES\_DESK\_OFFICE\_FEE,   
feeValues   
type 

}\]   
}\]   
}   
FUTURES\_EXCHANGE\_FEE, FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE, FUTURES\_PIT\_BROKERAGE\_FEE, FUTURES\_TRANSACTION\_FEE, LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE, GENERAL\_CHARGE, GST\_FEE, TAF\_FEE, INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX, UNKNOWN \] 

trueCommission \#/components/schemas/CommissionCommission { 

commissionLegs \[ \#/components/schemas/CommissionLegCommissionLeg { 

commissionValues \[ \#/components/schemas/CommissionValueCommissionValue { 

value number($double) 

type FeeTypestringEnum:   
\[ COMMISSION, SEC\_FEE, STR\_FEE, R\_FEE,   
CDSC\_FEE, OPT\_REG\_FEE, ADDITIONAL\_FEE, MISCELLANEOUS\_FEE, FTT, 

file:///Users/licaris/Downloads/account\_access.html 57/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides 

} 

} 

OrderActivity { 

activityTypestringEnum:   
FUTURES\_CLEARING\_FEE,   
FUTURES\_DESK\_OFFICE\_FEE,   
FUTURES\_EXCHANGE\_FEE,   
FUTURES\_GLOBEX\_FEE, FUTURES\_NFA\_FEE,   
FUTURES\_PIT\_BROKERAGE\_FEE,   
FUTURES\_TRANSACTION\_FEE,   
LOW\_PROCEEDS\_COMMISSION, BASE\_CHARGE,   
GENERAL\_CHARGE, GST\_FEE, TAF\_FEE,   
INDEX\_OPTION\_FEE, TEFRA\_TAX, STATE\_TAX,   
UNKNOWN \]   
}\]   
}\]   
} 

\[ EXECUTION, ORDER\_ACTION \] 

executionTypestringEnum:   
\[ FILL \] 

quantity number($double) 

orderRemainingQuantity number($double) 

\[   
xml: OrderedMap { "name": "executionLegs", "wrapped": true }   
\#/components/schemas/ExecutionLegExecutionLeg { 

legId integer($int64) 

executionLegs 

} 

ExecutionLeg {   
price number($double) quantity number($double) mismarkedQuantity number($double) instrumentId integer($int64) 

time string($date-time) }\] 

legId integer($int64) 

price number($double) 

quantity number($double) 

mismarkedQuantity number($double) 

instrumentId integer($int64) 

time string($date-time) 

} 

Position { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

file:///Users/licaris/Downloads/account\_access.html 58/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** Home   
description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { stringEnum:   
API Products User Guides 

assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped": true } \#/components/schemas/AccountAPIOptionDeliverableAccountAPIOptionDeliverable { symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum:   
\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double) 

} 

file:///Users/licaris/Downloads/account\_access.html 59/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal ServiceError {   
Developer Portal   
**Charles**   
message string   
**Schwab**   
**Logo Developer Portal**   
errors \[string\] 

}   
Home   
OrderLegCollection {   
API Products 

stringEnum:   
orderLegType   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY,   
User Guides   
COLLECTIVE\_INVESTMENT \] 

legId integer($int64) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

optionDeliverables \[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped": true }   
\#/components/schemas/AccountAPIOptionDeliverableAccountAPIOptionDeliverable { 

symbol string($int64) 

deliverableUnits number($double) 

file:///Users/licaris/Downloads/account\_access.html 60/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
apiCurrencyTypestringEnum:   
\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
**Logo Developer Portal** 

Home 

API Products 

assetType }\]   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

User Guides instruction   
putCallstringEnum:   
\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

instructionstringEnum:   
\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

positionEffectstringEnum:   
\[ OPENING, CLOSING, AUTOMATIC \] 

quantity number($double) 

quantityTypestringEnum:   
\[ ALL\_SHARES, DOLLARS, SHARES \] 

divCapGainsstringEnum:   
\[ REINVEST, PAYOUT \] 

toSymbol string 

} 

SecuritiesAccount { 

oneOf \-\>   
\#/components/schemas/MarginAccountMarginAccount { 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

isClosingOnlyRestrictedboolean   
default: false 

pfcbFlagboolean   
default: false 

positions \[ \#/components/schemas/PositionPosition { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

file:///Users/licaris/Downloads/account\_access.html 61/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { stringEnum:   
**Logo Developer Portal** Home 

assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

API Products User Guides   
cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTM 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "w   
\#/components/schemas/AccountAPIOptionDeliverableAccountA   
{   
symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPT FOREX, INDEX, CASH\_EQUIVALEN FIXED\_INCOME, PRODUCT, CURR COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double)   
}\] 

initialBalances \#/components/schemas/MarginInitialBalanceMarginInitialBalance { 

accruedInterest number($double) 

availableFundsNonMarginableTrade number($double) 

file:///Users/licaris/Downloads/account\_access.html 62/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides 

currentBalances   
bondValue number($double) buyingPower number($double) cashBalance number($double) cashAvailableForTrading number($double) cashReceipts number($double) dayTradingBuyingPower number($double) dayTradingBuyingPowerCall number($double) dayTradingEquityCall number($double) equity number($double) equityPercentage number($double) liquidationValue number($double) longMarginValue number($double) longOptionMarketValue number($double) longStockValue number($double) maintenanceCall number($double) maintenanceRequirement number($double) margin number($double) marginEquity number($double) moneyMarketFund number($double) mutualFundValue number($double) regTCall number($double) shortMarginValue number($double) shortOptionMarketValue number($double) shortStockValue number($double) totalCash number($double) isInCall number($double) unsettledCash number($double) pendingDeposits number($double) marginBalance number($double) shortBalance number($double) accountValue number($double) } 

\#/components/schemas/MarginBalanceMarginBalance { availableFunds number($double) availableFundsNonMarginableTrade number($double) buyingPower number($double) buyingPowerNonMarginableTrade number($double) dayTradingBuyingPower number($double) dayTradingBuyingPowerCall number($double) equity number($double) equityPercentage number($double) longMarginValue number($double) maintenanceCall number($double) maintenanceRequirement number($double) marginBalance number($double) regTCall number($double) shortBalance number($double) shortMarginValue number($double) sma number($double) isInCall number($double) stockBuyingPower number($double) optionBuyingPower number($double) } 

projectedBalances \#/components/schemas/MarginBalanceMarginBalance { 

availableFunds number($double) 

availableFundsNonMarginableTrade number($double) 

buyingPower number($double) 

buyingPowerNonMarginableTrade number($double) 

dayTradingBuyingPower number($double) 

dayTradingBuyingPowerCall number($double) 

equity number($double) 

equityPercentage number($double) 

longMarginValue number($double) 

maintenanceCall number($double) 

maintenanceRequirement number($double) 

marginBalance number($double) 

file:///Users/licaris/Downloads/account\_access.html 63/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides   
regTCall number($double) shortBalance number($double) shortMarginValue number($double) sma number($double) isInCall number($double) stockBuyingPower number($double) optionBuyingPower number($double) } 

}\#/components/schemas/CashAccountCashAccount { 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

isClosingOnlyRestrictedboolean   
default: false 

pfcbFlagboolean   
default: false 

positions \[ \#/components/schemas/PositionPosition { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQU FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT 

file:///Users/licaris/Downloads/account\_access.html 64/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides   
cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTM 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "w   
\#/components/schemas/AccountAPIOptionDeliverableAccountA   
{   
symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPT FOREX, INDEX, CASH\_EQUIVALEN FIXED\_INCOME, PRODUCT, CURR COLLECTIVE\_INVESTMENT \] 

initialBalances   
\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double)   
}\] 

\#/components/schemas/CashInitialBalanceCashInitialBalance { 

accruedInterest number($double) 

cashAvailableForTrading number($double) 

cashAvailableForWithdrawal number($double) 

cashBalance number($double) 

bondValue number($double) 

cashReceipts number($double) 

liquidationValue number($double) 

longOptionMarketValue number($double) 

longStockValue number($double) 

moneyMarketFund number($double) 

mutualFundValue number($double) 

shortOptionMarketValue number($double) 

shortStockValue number($double) 

isInCall number($double) 

unsettledCash number($double) 

cashDebitCallValue number($double) 

pendingDeposits number($double) 

accountValue number($double)   
} 

file:///Users/licaris/Downloads/account\_access.html 65/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal**   
\#/components/schemas/CashBalanceCashBalance { cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashCall number($double) 

Home   
currentBalances 

longNonMarginableMarketValue number($double) 

API Products 

User Guides 

projectedBalances 

} 

} 

SecuritiesAccountBase {   
totalCash number($double) cashDebitCallValue number($double) unsettledCash number($double) } 

\#/components/schemas/CashBalanceCashBalance { cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashCall number($double) longNonMarginableMarketValue number($double) totalCash number($double) cashDebitCallValue number($double) unsettledCash number($double) } 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

isClosingOnlyRestrictedboolean   
default: false 

pfcbFlagboolean   
default: false 

positions \[ \#/components/schemas/PositionPosition { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNO }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

file:///Users/licaris/Downloads/account\_access.html 66/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products 

User Guides   
symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIV FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped \#/components/schemas/AccountAPIOptionDeliverableAccountAPIOption 

{   
symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FU FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double)   
}\] 

} 

MarginAccount { 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

file:///Users/licaris/Downloads/account\_access.html 67/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal isClosingOnlyRestrictedboolean   
Developer Portal   
**Charles Schwab** 

default: false 

pfcbFlagboolean   
**Logo Developer Portal**   
default: false 

positions \[ \#/components/schemas/PositionPosition {   
Home 

API Products User Guides   
shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNO }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIV FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

file:///Users/licaris/Downloads/account\_access.html 68/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
**Logo Developer Portal** 

Home 

API Products   
\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped \#/components/schemas/AccountAPIOptionDeliverableAccountAPIOption { 

symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
User Guides   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FU FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

longOpenProfitLoss number($double) 

shortOpenProfitLoss number($double) 

previousSessionLongQuantity number($double) 

previousSessionShortQuantity number($double) 

currentDayCost number($double)   
}\] 

initialBalances \#/components/schemas/MarginInitialBalanceMarginInitialBalance { 

accruedInterest number($double) 

availableFundsNonMarginableTrade number($double) 

bondValue number($double) 

buyingPower number($double) 

cashBalance number($double) 

cashAvailableForTrading number($double) 

cashReceipts number($double) 

dayTradingBuyingPower number($double) 

dayTradingBuyingPowerCall number($double) 

dayTradingEquityCall number($double) 

equity number($double) 

equityPercentage number($double) 

liquidationValue number($double) 

longMarginValue number($double) 

longOptionMarketValue number($double) 

longStockValue number($double) 

maintenanceCall number($double) 

maintenanceRequirement number($double) 

margin number($double) 

marginEquity number($double) 

moneyMarketFund number($double) 

mutualFundValue number($double) 

regTCall number($double) 

shortMarginValue number($double) 

shortOptionMarketValue number($double) 

shortStockValue number($double) 

totalCash number($double) 

isInCall number($double) 

unsettledCash number($double) 

pendingDeposits number($double) 

marginBalance number($double) 

shortBalance number($double) 

accountValue number($double) 

file:///Users/licaris/Downloads/account\_access.html 69/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
} 

\#/components/schemas/MarginBalanceMarginBalance { availableFunds number($double)   
**Logo Developer Portal**   
availableFundsNonMarginableTrade number($double) 

Home 

API Products 

User Guides 

currentBalances 

projectedBalances 

} 

MarginInitialBalance {   
buyingPower number($double) buyingPowerNonMarginableTrade number($double) dayTradingBuyingPower number($double) dayTradingBuyingPowerCall number($double) equity number($double) equityPercentage number($double) longMarginValue number($double) maintenanceCall number($double) maintenanceRequirement number($double) marginBalance number($double) regTCall number($double) shortBalance number($double) shortMarginValue number($double) sma number($double) isInCall number($double) stockBuyingPower number($double) optionBuyingPower number($double) } 

\#/components/schemas/MarginBalanceMarginBalance { availableFunds number($double) availableFundsNonMarginableTrade number($double) buyingPower number($double) buyingPowerNonMarginableTrade number($double) dayTradingBuyingPower number($double) dayTradingBuyingPowerCall number($double) equity number($double) equityPercentage number($double) longMarginValue number($double) maintenanceCall number($double) maintenanceRequirement number($double) marginBalance number($double) regTCall number($double) shortBalance number($double) shortMarginValue number($double) sma number($double) isInCall number($double) stockBuyingPower number($double) optionBuyingPower number($double) } 

accruedInterest number($double) 

availableFundsNonMarginableTrade number($double) 

bondValue number($double) 

buyingPower number($double) 

cashBalance number($double) 

cashAvailableForTrading number($double) 

cashReceipts number($double) 

dayTradingBuyingPower number($double) 

dayTradingBuyingPowerCall number($double) 

dayTradingEquityCall number($double) 

equity number($double) 

equityPercentage number($double) 

liquidationValue number($double) 

longMarginValue number($double) 

longOptionMarketValue number($double) 

longStockValue number($double) 

maintenanceCall number($double) 

maintenanceRequirement number($double) 

margin number($double) 

marginEquity number($double) 

moneyMarketFund number($double) 

file:///Users/licaris/Downloads/account\_access.html 70/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal mutualFundValue number($double)   
Developer Portal   
**Charles**   
regTCall number($double)   
**Schwab**   
shortMarginValue number($double)   
**Logo Developer Portal**   
shortOptionMarketValue number($double) 

shortStockValue number($double)   
Home   
totalCash number($double)   
API Products   
isInCall number($double)   
User Guides   
~~unsettledCa~~sh number($double) 

pendingDeposits number($double) 

marginBalance number($double) 

shortBalance number($double) 

accountValue number($double) 

} 

MarginBalance { 

availableFunds number($double) 

availableFundsNonMarginableTrade number($double) 

buyingPower number($double) 

buyingPowerNonMarginableTrade number($double) 

dayTradingBuyingPower number($double) 

dayTradingBuyingPowerCall number($double) 

equity number($double) 

equityPercentage number($double) 

longMarginValue number($double) 

maintenanceCall number($double) 

maintenanceRequirement number($double) 

marginBalance number($double) 

regTCall number($double) 

shortBalance number($double) 

shortMarginValue number($double) 

sma number($double) 

isInCall number($double) 

stockBuyingPower number($double) 

optionBuyingPower number($double) 

} 

CashAccount { 

typestringEnum:   
\[ CASH, MARGIN \] 

accountNumber string 

roundTrips integer($int32) 

isDayTrader boolean   
default: false 

isClosingOnlyRestrictedboolean   
default: false 

pfcbFlagboolean   
default: false 

positions \[ \#/components/schemas/PositionPosition { 

shortQuantity number($double) 

averagePrice number($double) 

currentDayProfitLoss number($double) 

currentDayProfitLossPercentage number($double) 

longQuantity number($double) 

settledLongQuantity number($double) 

settledShortQuantity number($double) 

agedQuantity number($double) 

instrument \#/components/schemas/AccountsInstrumentAccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNO   
} 

file:///Users/licaris/Downloads/account\_access.html 71/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
\#/components/schemas/AccountEquityAccountEquity { stringEnum:   
**Schwab**   
**Logo Developer Portal** 

assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

Home 

API Products User Guides   
cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALEN FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountOptionAccountOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIV FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped \#/components/schemas/AccountAPIOptionDeliverableAccountAPIOption 

{   
symbol string($int64) 

deliverableUnits number($double) 

apiCurrencyTypestringEnum:   
optionDeliverables 

assetType 

}\] 

putCallstringEnum: 

\[ USD, CAD, EUR, JPY \] 

assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FU FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
}   
} 

marketValue number($double) 

maintenanceRequirement number($double) 

averageLongPrice number($double) 

averageShortPrice number($double) 

taxLotAverageLongPrice number($double) 

taxLotAverageShortPrice number($double) 

file:///Users/licaris/Downloads/account\_access.html 72/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal   
**Charles**   
**Schwab**   
longOpenProfitLoss number($double) shortOpenProfitLoss number($double) previousSessionLongQuantity number($double)   
**Logo Developer Portal**   
previousSessionShortQuantity number($double) 

Home 

API Products 

User Guides 

initialBalances 

currentBalances 

projectedBalances 

} 

CashInitialBalance {   
currentDayCost number($double) }\] 

\#/components/schemas/CashInitialBalanceCashInitialBalance { accruedInterest number($double) cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashBalance number($double) bondValue number($double) cashReceipts number($double) liquidationValue number($double) longOptionMarketValue number($double) longStockValue number($double) moneyMarketFund number($double) mutualFundValue number($double) shortOptionMarketValue number($double) shortStockValue number($double) isInCall number($double) unsettledCash number($double) cashDebitCallValue number($double) pendingDeposits number($double) accountValue number($double) } 

\#/components/schemas/CashBalanceCashBalance { cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashCall number($double) longNonMarginableMarketValue number($double) totalCash number($double) cashDebitCallValue number($double) unsettledCash number($double) } 

\#/components/schemas/CashBalanceCashBalance { cashAvailableForTrading number($double) cashAvailableForWithdrawal number($double) cashCall number($double) longNonMarginableMarketValue number($double) totalCash number($double) cashDebitCallValue number($double) unsettledCash number($double) } 

accruedInterest number($double) 

cashAvailableForTrading number($double) 

cashAvailableForWithdrawal number($double) 

cashBalance number($double) 

bondValue number($double) 

cashReceipts number($double) 

liquidationValue number($double) 

longOptionMarketValue number($double) 

longStockValue number($double) 

moneyMarketFund number($double) 

mutualFundValue number($double) 

shortOptionMarketValue number($double) 

shortStockValue number($double) 

isInCall number($double) 

unsettledCash number($double) 

cashDebitCallValue number($double) 

pendingDeposits number($double) 

accountValue number($double) 

} 

file:///Users/licaris/Downloads/account\_access.html 73/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal CashBalance {   
Developer Portal   
**Charles**   
cashAvailableForTrading number($double)   
**Schwab**   
**Logo Developer Portal**   
cashAvailableForWithdrawal number($double) 

cashCall number($double) 

Home   
longNonMarginableMarketValue number($double) 

totalCash number($double)   
API Products   
cashDebitCallValue number($double)   
User Guides   
unsettledCash number($double) 

} 

TransactionBaseInstrument { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) } 

AccountsBaseInstrument { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) } 

AccountsInstrument { 

oneOf \-\>   
\#/components/schemas/AccountCashEquivalentAccountCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] }\#/components/schemas/AccountEquityAccountEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/AccountFixedIncomeAccountFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

maturityDate string($date-time) 

factor number($double) 

variableRate number($double) 

}\#/components/schemas/AccountMutualFundAccountMutualFund { 

assetType\* stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, 

file:///Users/licaris/Downloads/account\_access.html 74/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
COLLECTIVE\_INVESTMENT \] 

cusip string   
**Schwab**   
symbol string   
**Logo Developer Portal** description string 

Home   
instrumentId integer($int64) netChange number($double)   
API Products   
~~}\#/co~~mponents/schemas/AccountOptionAccountOption { User Guides 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped": true } \#/components/schemas/AccountAPIOptionDeliverableAccountAPIOptionDeliverable { symbol string($int64) 

deliverableUnits number($double)   
optionDeliverables   
apiCurrencyTypestringEnum:   
\[ USD, CAD, EUR, JPY \] 

assetType 

}\] 

putCallstringEnum:   
assetTypestringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

optionMultiplier integer($int32) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string   
} 

} 

TransactionInstrument { 

oneOf \-\>   
\#/components/schemas/TransactionCashEquivalentTransactionCashEquivalent { stringEnum:   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] }\#/components/schemas/CollectiveInvestmentCollectiveInvestment { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ UNIT\_INVESTMENT\_TRUST, EXCHANGE\_TRADED\_FUND, CLOSED\_END\_FUND, INDEX, UNITS \] }\#/components/schemas/CurrencyCurrency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double)   
} 

file:///Users/licaris/Downloads/account\_access.html 75/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal \#/components/schemas/TransactionEquityTransactionEquity {   
Developer Portal   
**Charles**   
**Schwab**   
assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY,   
**Logo Developer Portal**   
COLLECTIVE\_INVESTMENT \] 

Home   
cusip string symbol string   
API Products   
~~descr~~iption string   
User Guides   
~~instru~~mentId integer($int64) 

netChange number($double) stringEnum: 

type   
\[ COMMON\_STOCK, PREFERRED\_STOCK, DEPOSITORY\_RECEIPT, PREFERRED\_DEPOSITORY\_RECEIPT, RESTRICTED\_STOCK, COMPONENT\_UNIT, RIGHT, WARRANT, CONVERTIBLE\_PREFERRED\_STOCK, CONVERTIBLE\_STOCK, LIMITED\_PARTNERSHIP, WHEN\_ISSUED, UNKNOWN \] 

}\#/components/schemas/TransactionFixedIncomeTransactionFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

stringEnum:   
\[ BOND\_UNIT, CERTIFICATE\_OF\_DEPOSIT, CONVERTIBLE\_BOND, COLLATERALIZED\_MORTGAGE\_OBLIGATION,   
CORPORATE\_BOND, GOVERNMENT\_MORTGAGE, GNMA\_BONDS, MUNICIPAL\_ASSESSMENT\_DISTRICT, MUNICIPAL\_BOND, OTHER\_GOVERNMENT, SHORT\_TERM\_PAPER, US\_TREASURY\_BOND, US\_TREASURY\_BILL,   
type 

US\_TREASURY\_NOTE, US\_TREASURY\_ZERO\_COUPON, AGENCY\_BOND, WHEN\_AS\_AND\_IF\_ISSUED\_BOND, ASSET\_BACKED\_SECURITY, UNKNOWN \] 

maturityDate string($date-time) factor number($double) multiplier number($double) variableRate number($double) }\#/components/schemas/ForexForex { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ STANDARD, NBBO, UNKNOWN \] 

\#/components/schemas/CurrencyCurrency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

baseCurrency   
cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) } 

\#/components/schemas/CurrencyCurrency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string   
counterCurrency   
symbol string 

description string 

instrumentId integer($int64) 

netChange number($double)   
} 

}\#/components/schemas/FutureFuture { 

activeContract boolean   
default: false 

typestringEnum:   
\[ STANDARD, UNKNOWN \] 

file:///Users/licaris/Downloads/account\_access.html 76/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal expirationDate string($date-time)   
Developer Portal   
**Charles**   
lastTradingDate string($date-time)   
**Schwab**   
firstNoticeDate string($date-time)   
**Logo Developer Portal**   
multiplier number($double) 

Home   
}\#/components/schemas/IndexIndex {   
API Products   
activeContract boolean   
User Guides 

default: false 

typestringEnum:   
\[ BROAD\_BASED, NARROW\_BASED, UNKNOWN \] 

}\#/components/schemas/TransactionMutualFundTransactionMutualFund { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) fundFamilyName string 

fundFamilySymbol string 

fundGroup string 

type   
stringEnum:   
\[ NOT\_APPLICABLE, OPEN\_END\_NON\_TAXABLE, OPEN\_END\_TAXABLE, NO\_LOAD\_NON\_TAXABLE, NO\_LOAD\_TAXABLE, UNKNOWN \] 

exchangeCutoffTime string($date-time) purchaseCutoffTime string($date-time) redemptionCutoffTime string($date-time) }\#/components/schemas/TransactionOptionTransactionOption { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

expirationDate string($date-time) 

\[   
xml: OrderedMap { "name": "optionDeliverables", "wrapped": true }   
\#/components/schemas/TransactionAPIOptionDeliverableTransactionAPIOptionDeliverable { 

rootSymbol string 

strikePercent integer($int64) 

deliverableNumber integer($int64) 

optionDeliverables   
deliverableUnits number($double) deliverable{ 

} 

assetTypestringEnum: 

assetType 

}\] 

optionPremiumMultiplier integer($int64) putCallstringEnum:   
\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

\[ PUT, CALL, UNKNOWN \] 

strikePrice number($double) 

typestringEnum:   
\[ VANILLA, BINARY, BARRIER, UNKNOWN \] 

underlyingSymbol string 

underlyingCusip string 

deliverable{   
} 

}\#/components/schemas/ProductProduct { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

file:///Users/licaris/Downloads/account\_access.html 77/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal symbol string   
Developer Portal   
**Charles**   
description string   
**Schwab**   
instrumentId integer($int64)   
**Logo Developer Portal**   
netChange number($double) 

Home   
typestringEnum:   
API Products ~~}~~   
User Guides }   
\[ TBD, UNKNOWN \] 

TransactionCashEquivalent { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \] } 

CollectiveInvestment { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ UNIT\_INVESTMENT\_TRUST, EXCHANGE\_TRADED\_FUND, CLOSED\_END\_FUND, INDEX, UNITS \] } 

instruction stringEnum: 

\[ BUY, SELL, BUY\_TO\_COVER, SELL\_SHORT, BUY\_TO\_OPEN, BUY\_TO\_CLOSE, SELL\_TO\_OPEN, SELL\_TO\_CLOSE, EXCHANGE, SELL\_SHORT\_EXEMPT \] 

assetType stringEnum: 

\[ EQUITY, MUTUAL\_FUND, OPTION, FUTURE, FOREX, INDEX, CASH\_EQUIVALENT, FIXED\_INCOME, PRODUCT, CURRENCY, COLLECTIVE\_INVESTMENT \] 

Currency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) } 

TransactionEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) stringEnum: 

type }   
\[ COMMON\_STOCK, PREFERRED\_STOCK, DEPOSITORY\_RECEIPT, PREFERRED\_DEPOSITORY\_RECEIPT, RESTRICTED\_STOCK, COMPONENT\_UNIT, RIGHT, WARRANT, CONVERTIBLE\_PREFERRED\_STOCK, CONVERTIBLE\_STOCK, LIMITED\_PARTNERSHIP, WHEN\_ISSUED, UNKNOWN \] 

TransactionFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

file:///Users/licaris/Downloads/account\_access.html 78/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal description string   
Developer Portal   
**Charles**   
instrumentId integer($int64)   
**Schwab**   
netChange number($double)   
**Logo Developer Portal**   
stringEnum: 

Home type   
\[ BOND\_UNIT, CERTIFICATE\_OF\_DEPOSIT, CONVERTIBLE\_BOND, COLLATERALIZED\_MORTGAGE\_OBLIGATION, CORPORATE\_BOND, GOVERNMENT\_MORTGAGE, GNMA\_BONDS, MUNICIPAL\_ASSESSMENT\_DISTRICT, MUNICIPAL\_BOND, OTHER\_GOVERNMENT, SHORT\_TERM\_PAPER, US\_TREASURY\_BOND, US\_TREASURY\_BILL,   
API Products 

User Guides   
US\_TREASURY\_NOTE, US\_TREASURY\_ZERO\_COUPON, AGENCY\_BOND, WHEN\_AS\_AND\_IF\_ISSUED\_BOND, ASSET\_BACKED\_SECURITY, UNKNOWN \] 

maturityDate string($date-time) factor number($double) multiplier number($double) variableRate number($double) } 

Forex { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ STANDARD, NBBO, UNKNOWN \] 

\#/components/schemas/CurrencyCurrency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

baseCurrency   
cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) } 

\#/components/schemas/CurrencyCurrency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string   
counterCurrency   
symbol string 

description string 

instrumentId integer($int64) 

netChange number($double)   
} 

} 

Future { 

activeContract boolean   
default: false 

typestringEnum:   
\[ STANDARD, UNKNOWN \] 

expirationDate string($date-time) 

lastTradingDate string($date-time) 

firstNoticeDate string($date-time) 

multiplier number($double) 

oneOf \-\> \#/components/schemas/TransactionCashEquivalentTransactionCashEquivalent { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ SWEEP\_VEHICLE, SAVINGS, MONEY\_MARKET\_FUND, UNKNOWN \]   
} 

file:///Users/licaris/Downloads/account\_access.html 79/102  
2/26/26, 6:59 PM Trader API \- Individual | Products | Charles Schwab Developer Portal 

Developer Portal **Charles**   
\#/components/schemas/CollectiveInvestmentCollectiveInvestment { stringEnum:   
**Schwab**   
assetType\*   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY,   
**Logo Developer Portal**   
COLLECTIVE\_INVESTMENT \] 

Home 

API Products User Guides   
cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ UNIT\_INVESTMENT\_TRUST, EXCHANGE\_TRADED\_FUND, CLOSED\_END\_FUND, INDEX, UNITS \] }\#/components/schemas/CurrencyCurrency { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

}\#/components/schemas/TransactionEquityTransactionEquity { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) netChange number($double) stringEnum: 

type   
\[ COMMON\_STOCK, PREFERRED\_STOCK, DEPOSITORY\_RECEIPT, PREFERRED\_DEPOSITORY\_RECEIPT, RESTRICTED\_STOCK, COMPONENT\_UNIT, RIGHT, WARRANT, CONVERTIBLE\_PREFERRED\_STOCK, CONVERTIBLE\_STOCK, LIMITED\_PARTNERSHIP, WHEN\_ISSUED, UNKNOWN \] 

}\#/components/schemas/TransactionFixedIncomeTransactionFixedIncome { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

stringEnum:   
\[ BOND\_UNIT, CERTIFICATE\_OF\_DEPOSIT, CONVERTIBLE\_BOND, 

type   
COLLATERALIZED\_MORTGAGE\_OBLIGATION, CORPORATE\_BOND, GOVERNMENT\_MORTGAGE, GNMA\_BONDS, MUNICIPAL\_ASSESSMENT\_DISTRICT, MUNICIPAL\_BOND, OTHER\_GOVERNMENT, SHORT\_TERM\_PAPER, US\_TREASURY\_BOND, US\_TREASURY\_BILL, US\_TREASURY\_NOTE, US\_TREASURY\_ZERO\_COUPON, AGENCY\_BOND, WHEN\_AS\_AND\_IF\_ISSUED\_BOND, ASSET\_BACKED\_SECURITY, UNKNOWN \] 

maturityDate string($date-time) factor number($double) multiplier number($double) variableRate number($double) }\#/components/schemas/ForexForex { 

assetType\*   
stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, CURRENCY, COLLECTIVE\_INVESTMENT \] 

cusip string 

symbol string 

description string 

instrumentId integer($int64) 

netChange number($double) 

typestringEnum:   
\[ STANDARD, NBBO, UNKNOWN \] 

baseCurrency \#/components/schemas/CurrencyCurrency { 

assetType\* stringEnum:   
\[ EQUITY, OPTION, INDEX, MUTUAL\_FUND, CASH\_EQUIVALENT, FIXED\_INCOME, 

file:///Users/licaris/Downloads/account\_access.html 80/102