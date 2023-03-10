# Introduction
This repository sends stocks and crypto market data to channels like telegram

## Features
* Stocks
  * Basic info such as closing price, EMA20, closing price to EMA20 delta
  * Overextension from EMA20 based on the median delta when stock reverse in the next few days
* Crypto
  * BTC Exchange netflow, supply, median trade intensity 

![Test](https://github.com/hanchiang/market-data-notification/actions/workflows/test.yml/badge.svg)
![Deploy](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy.yml/badge.svg)
![Deploy cron stocks](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-stocks.yml/badge.svg)
![Deploy cron crypto](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-crypto.yml/badge.svg)
![Deploy common](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-common-cron.yml/badge.svg)

# Tech stack
* Language: Python
* Framework: FastAPI

# Structure
* `src`
  * `server`: API server
  * `job`: Scheduled jobs that sends stocks and crypto notification

# Example message for stocks
## Test message
![stocks test message](images/telegram_stocks_test_message.png)

## Real message
![stocks real message](images/telegram_stocks_real_message.png)

# Example message for crypto
## Real message
![crypto real message](images/telegram_crypto_message.png)

# Stocks cron workflow
* Receive market data when market closes -> save in redis
* Scheduled job before market open -> Send notification to telegram

![Stocks data workflow](images/tradingview-daily-stocks-info.png)

# Crypto cron workflow
* Scheduled job send notification to telegram

# Common cron workflow
* Send redis data via email

# TODO
* diagram of workflow
* Common cron: send attachment in email
* Test