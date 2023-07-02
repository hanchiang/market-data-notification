![Test](https://github.com/hanchiang/market-data-notification/actions/workflows/test.yml/badge.svg)
![Deploy](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy.yml/badge.svg)
![Deploy cron stocks](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-stocks.yml/badge.svg)
![Deploy cron crypto](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-crypto.yml/badge.svg)
![Deploy common](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-common-cron.yml/badge.svg)

# Introduction
This repository sends stocks and crypto market data to channels like telegram

## Features
* Stocks
  * Basic info such as closing price, EMA20, difference between closing price and EMA20
  * [Overextension from EMA20 based on the median delta when stock reverse in the next few days](https://github.com/hanchiang/market-data-notification/blob/master/CONTRIBUTING.md#overextendedpositive--negative-levels-from-ema20)
  * Highest volume in the past few consecutive days
  * Sudden large drop in VIX futures, or a decline for 5 consecutive days
* Crypto
  * BTC Exchange netflow, supply, median trade intensity, fees

# Tech stack
* Language: Python
* Framework: FastAPI
* Database: Redis

# Structure
* `src`
  * `server`: API server
  * `service`: For retrieving data from various sources
  * `job`: Scheduled jobs that sends stocks and crypto notification

# Example message for stocks
## EMA20 overextension, highest volume
![stocks ema overextension highest volume](images/stocks/tradingview-stocks-ema-overextension-highest-volume.png)
## VIX futures single day and consecutive days decrease
![stocks ema overextension highest volume](images/stocks/tradingview-vix-central-single-day-and-consecutive-days-decrease.png)


# Example message for crypto
![crypto exchange supply](images/telegram_crypto_exchange_supply.png)
![crypto fees](images/telegram_crypto_fees.png)

# How to do local development
## 1. No docker
* Install python: https://www.python.org/downloads/
* Install poetry: https://python-poetry.org/docs/
* Install dependencies: `poetry install`
* Run server: `poetry run python3 main.py`
* Run stocks job: `poetry run python src/job/stocks/stocks.py --force_run=1 --test_mode=1`
* Run crypto job: `poetry run python src/job/crypto/crypto.py --force_run=1 --test_mode=1`

## 2. With docker
* Install docker: https://docs.docker.com/engine/install/
* Run server: `docker-compose up -d`
* Run stocks job: `docker exec -it market_data_notification sh -c "ENV=dev poetry run python src/job/stocks/stocks.py --force_run=1 --test_mode=1"`
* Run crypto job: `docker exec -it market_data_notification sh -c "ENV=dev poetry run python src/job/crypto/crypto.py --force_run=1 --test_mode=1"`

## Run tests
* Run test: `coverage run --branch -m pytest`
* Coverage report: `coverage report --show-missing`

## Test TradingView webhook
Webhooks have to be a HTTPS URL, so localhost does not work.
* Use a reverse proxy like [ngrok](https://ngrok.com/)
* Set is_testing_telegram to TRUE, which will save data to a dev key in redis and send notification to a dev telegram channel

# Stocks cron workflow
* Receive market data when market closes -> save in redis
* Scheduled job before market open -> Send notification to telegram

![Stocks data workflow](images/tradingview-daily-stocks-info.png)

# Crypto cron workflow
* Scheduled job send notification to telegram

# Common cron workflow
* Send redis data via email

# Contributing
See [CONTRIBUTING](CONTRIBUTING.md)

# TODO
* API to send message to telegram, backup data
* Code coverage in github action
* Bot with pre-defined menu for user interaction
* Get overextended levels using google sheet API instead of manually entering them.
* log
* Delete old github package versions: https://github.com/actions/delete-package-versions
* Cache barchart stock price