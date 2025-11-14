# Home Assistant Electric Ireland Integration

[![Open Integration](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=badwinton&repository=Home-Assistant-Electric-Ireland&category=integration)

Home Assistant integration with **Electric Ireland insights**.

It is capable of:

* Reporting **consumed energy** in kWh.
* Reporting **usage cost** in EUR (see the FAQ below for more details on this).

It will also aggregate the report data into statistical buckets, so they can be fed into the Energy Dashboard.

![](https://i.imgur.com/6ew3JIf.png)

## FAQs

### How does it work?

It basically scrapes the Insights page that Electric Ireland provides. It will first mimic a user login interaction,
and then will navigate to the page to fetch the data.

As this data is also feed from ESB ([Electrical Supply Board](https://esb.ie)), it is not in real time. They publish
data with 1-3 days delay; this integration takes care of that and will fetch every hour and ingest data dated back up
to 10 days. This job runs every hour, so whenever it gets published it should get feed into Home Assistant within 60
minutes.

### Why not fetching from ESB directly?

I have Electric Ireland, and ESB has a captcha in their login. I just didn't want to bother to investigate how to
bypass it.

### Why not applying the 30% Off DD discount?

This is tariff-dependant. The Electric Ireland API reports cost as per tariff price (24h, smart, etc.), so in case some
tariff does not offer the 30% Off Direct Debit, this integration will apply a transformation incorrect for the user.

So, in summary: Cost reports gross usage cost with VAT, without discount but also without standing charge or levy.

### Why does the individual reporte device sometimes exceed the reported usage in Electric Ireland?

I don't have a clear answer to this. I have noticed this in some buckets, but there it is an issue in how the metrics
are reported into buckets. It is an issue either in ESB / Electric Ireland reporting, that they report the intervals
incorrectly; or it is the device meters that they may do the same.

In either case, I would not expect the total amount to differ: it is just a matter of consumption/cost being reported
into the wrong hour. If you take the previous and after, the total should be the same.

## Technical Details

### Sensors

- **Electric Ireland Consumption**: reports consumed data in kWh. This integration now retrieves hourly consumption
  data from the provider (via the `hourly-usage` endpoint).
- **Electric Ireland Cost**: reports the total cost charged per day (without discounts and without standing charge),
  using the `usage-daily` endpoint which returns daily usage/cost values.

Additionally, the integration can retrieve a bill projection using the `bill-projection` endpoint; this is not
exposed as a sensor by default but is available to the integration and can be added as a separate entity if
desired.

### Data Retrieval Flow

1. Open a `requests` session against Electric Ireland website, and:
    1. Create a GET request to retrieve the cookies and the state.
    2. Do a POST request to login into Electric Ireland.
    3. Scrape the dashboard to try to find the `div` with the target Account Number.
    4. Navigate to the Insights page for that Account Number.
2. Now, once we have that Insights page, we don't need the Electric Ireland session anymore:
  1. The page contains a payload to call the data API used by Electric Ireland (Bidgely or similar).
  2. Authenticate using that payload against the data API (no need for session or cookies).
  3. Send requests to the available endpoints to fetch the data. Current supported endpoints used by this
     integration are:

     - `MeterInsight/<partner>/<contract>/<premise>/usage-daily?start=<YYYY-MM-DD>&end=<YYYY-MM-DD>`
     - `MeterInsight/<partner>/<contract>/<premise>/hourly-usage?date=<YYYY-MM-DD>`
     - `MeterInsight/<partner>/<contract>/<premise>/bill-projection`

  4. Profit! 🎉

### Schedule

Every hour:

- Performs once the flow mentioned above to get the API credentials.
- Launches requests for the configured lookup range of days (default is the previous 10 days). For each day the
  integration will request:
  - hourly consumption data via `hourly-usage` (when applicable),
  - daily aggregated usage/cost via `usage-daily` (single-day range or larger windows).
- It will ingest the data using the interval end timestamp reported by the API.

## Acknowledgements

* [Historical sensors for Home Assistant](https://github.com/ldotlopez/ha-historical-sensor): provided the library and 
  skeleton to create the bare minimum working version.
