# Introduction
This repository sends stocks and crypto market data to channels like telegram

## Data sources
* Stocks: Trading view, vix central
* Crypto: Messari

![test](https://github.com/hanchiang/market-data-notification/actions/workflows/test.yml/badge.svg)
![deploy](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy.yml/badge.svg)
![deploy cron stocks](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-stocks.yml/badge.svg)
![DEPLOY cron crypto](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-crypto.yml/badge.svg)
![DEPLOY cron crypto](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-common-cron.yml/badge.svg)

# Tech stack
* Language: Python
* Framework: FastAPI

# Structure
* `src`
  * `server`: API server
  * `job`: Scheduled jobs that sends stocks and crypto notification

# Sample
## Stocks: Test message
![stocks test message](images/telegram_stocks_test_message.png)

## Stocks: Real message
![stocks real message](images/telegram_stocks_real_message.png)

## Crypto: Real message
![crypto real message](images/telegram_crypto_message.png)

# Stocks cron workflow
* Receive market data when market closes -> save in redis
* Scheduled job before market open -> Send notification to telegram

# Crypto cron workflow
* Scheduled job send notification to telegram

# Common cron workflow
* Send redis data via email