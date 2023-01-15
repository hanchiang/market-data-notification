# Introduction
This repository sends stocks and crypto market data to channels like telegram

## Data sources
* Stocks: Trading view, vix central
* Crypto: Messari

![example workflow](https://github.com/hanchiang/market-data-notification/actions/workflows/test.yml/badge.svg)
![example workflow](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy.yml/badge.svg)
![example workflow](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron.yml/badge.svg)

# Tech stack
* Language: Python
* Framework: FastAPI

# Sample
## Stocks: Test message
![stocks test message](images/telegram_stocks_test_message.png)

## Stocks: Real message
![stocks real message](images/telegram_stocks_real_message.png)

## Crypto: Real message
![crypto real message](images/telegram_crypto_message.png)

# Stocks workflow
* Receive market data when market closes at 4pm local time -> save in redis
* Scheduled job before market open -> Send notification to telegram

# Crypto workflow
* Scheduled job send notification to telegram