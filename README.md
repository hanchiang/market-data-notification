![Test](https://github.com/hanchiang/market-data-notification/actions/workflows/test.yml/badge.svg)
![Deploy](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy.yml/badge.svg)
![Deploy cron stocks](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-stocks.yml/badge.svg)
![Deploy cron crypto](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-cron-crypto.yml/badge.svg)
![Deploy common](https://github.com/hanchiang/market-data-notification/actions/workflows/deploy-email-backup-cron.yml/badge.svg)

# Introduction
This repository sends stocks and crypto market data to channels like telegram

## Features
* Stocks telegram channel: https://t.me/+6RjlDOi8OyxkOGU1
  * Basic info such as closing price, EMA20, difference between closing price and EMA20
  * [Overextension from EMA20 based on the median delta when stock reverse in the next few days](https://github.com/hanchiang/market-data-notification/blob/master/CONTRIBUTING.md#overextendedpositive--negative-levels-from-ema20)
  * Highest volume in the past few consecutive days
  * Sudden large drop in VIX futures, or a decline for 5 consecutive days
  * Fear greed index
* Crypto telegram channel: https://t.me/+geTqFk8RktA2YzA9
  * BTC Exchange netflow, supply
  * BTC median trade intensity, fees
  * Fear greed index

# Tech stack
* Language: Python
* Framework: FastAPI
* Database: Redis

# Structure
* `src`
  * `server`: API server
  * `service`: For retrieving data from various sources
  * `job`: Scheduled jobs that sends stocks and crypto notification

# Example messages
See [example messages](examples/MESSAGES.md) for stocks and crypto notifications.

# Data sources
**Crypto**
* CoinMarketCap
* Alternative.me

**Stocks**
* TradingView
* VIX central
* CNN fear greed

# Stocks cron workflow
* Receive market data when market closes -> save in redis
* Scheduled job before market open -> Send notification to telegram

# Crypto cron workflow
* Scheduled job send notification to telegram

# Common cron workflow
* Send redis data via email


```mermaid
flowchart TB
    subgraph External["External Systems"]
        TV["<b>Trading view</b><br/><b>Pinescript:</b> Gathers stocks data,<br/>triggers alert once per bar close<br/><b>Chart:</b> Add indicator to chart,<br/>add alert and configure webhook"]
        DS["<b>External data source library</b><br/>stocks: barchart, cnn fear greed<br/>crypto: messari, chainanalysis,<br/>cmc, alternativeme fear greed"]
    end

    subgraph Infra1["Infra - Before market open"]
        StartBefore["<b>Start script</b><br/>Before market open"]
        CronBefore["<b>Cron trigger</b><br/>before market open"]
        StopOpen["<b>Stop script</b><br/>After market open"]
    end
    
    subgraph Infra2["Infra - After market close"]
        StartClose["<b>Start script</b><br/>Before market close"]
        CronAfter["<b>Cron trigger</b><br/>after market close"]
        StopClose["<b>Stop script</b><br/>After market close"]
    end

    subgraph Server["Server"]
        Redis[("<b>Redis</b><br/>Selenium chromium")]
        
        subgraph Jobs["Market data notification"]
            StocksJob["<b>stocks.py</b><br/>Get trading view data from redis<br/>Get vix central data<br/>Get fear greed index"]
            CryptoJob["<b>crypto.py</b><br/>Get exchange flow from messari<br/>Get BTC fees from chainanalysis<br/>Get fear greed index"]
            ShellScript["<b>Shell script</b><br/>Backup redis data<br/>via email attachment"]
        end
    end

    Telegram["<b>Telegram channel</b>"]
    Email["<b>Email provider</b>"]

    %% Purple flow
    StartBefore -.->|"1"| CronBefore
    CronBefore -.->|"2"| StocksJob
    CronBefore -.->|"2"| CryptoJob
    Redis -.-> StocksJob
    DS --> StocksJob
    DS --> CryptoJob
    StocksJob -.-> Telegram
    CryptoJob -.-> Telegram
    StopOpen -.->|"3"| StocksJob
    StopOpen -.->|"3"| CryptoJob
    
    %% Green flow
    TV -->|"2"| StocksJob
    StocksJob -->|"2"| Redis
    StartClose -->|"1"| CronAfter
    CronAfter -->|"3"| ShellScript
    ShellScript --> Email
    StopClose -->|"4"| ShellScript

    %% Color styling
    linkStyle 0,1,2,3,4,5,6,7,8,9 stroke:#9370DB,stroke-width:3px
    linkStyle 10,11,12,13,14,15 stroke:#90EE90,stroke-width:3px
    
    style TV fill:#FFE4B5
    style DS fill:#FFE4B5
    style Telegram fill:#ADD8E6
    style Email fill:#ADD8E6
    style Infra1 fill:#F0E6FF
    style Infra2 fill:#E6FFE6
```
* purple flow: send notification before market open
* green flow: send notification, save tradingview data, backup redis data when market close

# How to do local development
## Note
`market_data_library` is currently not a public python package. 

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
* Set `is_testing_telegram` to 'true', which will save data to a dev key in redis and send notification to a dev telegram channel

# Contributing
See [CONTRIBUTING](CONTRIBUTING.md)

# TODO
* API to send message to telegram, backup data
* Code coverage in github action
* Bot with pre-defined menu for user interaction
* log
* Cache barchart stock price