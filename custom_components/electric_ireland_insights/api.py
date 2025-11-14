import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests import RequestException

from .const import DOMAIN

LOGGER = logging.getLogger(DOMAIN)


class ElectricIrelandScraper:
    def __init__(self, username, password, account_number):
        self.__session = None
        self.__premise = None
        self.__partner = None
        self.__contract = None

        self.__username = username
        self.__password = password
        self.__account_number = account_number

    def refresh_credentials(self):
        LOGGER.info("Trying to refresh credentials...")
        session = requests.Session()

        # Get the premise, partner, and contract from the Insights page
        premise, partner, contract = self.__get_meter_insight_params(session)
        if not all([premise, partner, contract]):
            return

        self.__session = session
        self.__premise = premise
        self.__partner = partner
        self.__contract = contract

    @property
    def session(self):
        if not self.__session:
            self.refresh_credentials()
        return self.__session

    def __get_meter_insight_params(self, session):
        """Get premise, partner, and contract from the Insights page."""
        # REQUEST 1: Get the Source token, and initialize the session
        LOGGER.debug("Getting Source Token...")
        res1 = session.get("https://youraccountonline.electricireland.ie/", allow_redirects=True)
        try:
            res1.raise_for_status()
        except RequestException as err:
            LOGGER.error(f"Failed to Get Source Token: {err}")
            return None

        # Get rvt cookie from the response (it should be set by the initial request)
        rvt = session.cookies.get("rvt") or session.cookies.get_dict().get("rvt")

        soup1 = BeautifulSoup(res1.text, "html.parser")
        source_input = soup1.find('input', attrs={'name': 'Source'})
        
        if not source_input:
            LOGGER.error("Could not retrieve Source input field")
            return None
        
        source = source_input.get('value')
        if not source:
            LOGGER.error("Could not retrieve Source value")
            return None
        
        if not rvt:
            LOGGER.error("Could not find rvt cookie")
            return None

        # REQUEST 2: Perform Login (this will return a 302 redirect to /Accounts/Init)
        LOGGER.debug("Performing Login...")
        res2 = session.post(
            "https://youraccountonline.electricireland.ie/",
            data={
                "LoginFormData.UserName": self.__username,
                "LoginFormData.Password": self.__password,
                "rvt": rvt,
                "Source": source,
                "PotText": "",
                "__EiTokPotText": "",
                "ReturnUrl": "",
                "AccountNumber": "",
            },
            allow_redirects=True,  # Follow the 302 redirect to /Accounts/Init
        )
        try:
            res2.raise_for_status()
        except RequestException as err:
            LOGGER.error(f"Failed to Perform Login: {err}")
            return None

        # The redirect should take us to /Accounts/Init, then we need to get the accounts page
        if "/Accounts/Init" in res2.url:
            res2 = session.get("https://youraccountonline.electricireland.ie/Accounts", allow_redirects=True)
            res2.raise_for_status()

        soup2 = BeautifulSoup(res2.text, "html.parser")
        account_divs = soup2.find_all("div", {"class": "my-accounts__item"})
        target_account = None
        for account_div in account_divs:
            account_number = account_div.find("p", {"class": "account-number"}).text
            if account_number != self.__account_number:
                LOGGER.debug(f"Skipping account {account_number} as it is not target")
                continue

            is_elec_divs = account_div.find_all("h2", {"class": "account-electricity-icon"})
            if len(is_elec_divs) != 1:
                LOGGER.info(f"Found account {account_number} but is not Electricity")
                continue

            target_account = account_div
            break

        if not target_account:
            LOGGER.warning("Failed to find Target Account; please verify it is the correct one")
            return None

        # REQUEST 3: Navigate to Insights page to get meter parameters
        LOGGER.debug("Navigating to Insights page...")
        event_form = target_account.find("form", {"action": "/Accounts/OnEvent"})
        req3 = {"triggers_event": "AccountSelection.ToInsights"}
        for form_input in event_form.find_all("input"):
            req3[form_input.get("name")] = form_input.get("value")

        res3 = session.post(
            "https://youraccountonline.electricireland.ie/Accounts/OnEvent",
            data=req3,
        )
        try:
            res3.raise_for_status()
        except RequestException as err:
            LOGGER.error(f"Failed to Perform Insights Navigation: {err}")
            return None

        soup3 = BeautifulSoup(res3.text, "html.parser")
        
        # Extract data attributes from modelData div
        model_data_div = soup3.find("div", {"id": "modelData"})
        
        if not model_data_div:
            LOGGER.error("Failed to find modelData div")
            return None, None, None
        
        premise = model_data_div.get("data-premise")
        partner = model_data_div.get("data-partner")
        contract = model_data_div.get("data-contract")
        
        if not all([premise, partner, contract]):
            LOGGER.error("Missing required parameters in modelData div")
            return None, None, None
        
        LOGGER.debug(f"Extracted meter parameters: premise={premise}, partner={partner}, contract={contract}")
        return premise, partner, contract


    def get_data(self, target_date, is_granular=False):
        """Get usage data using the MeterInsight API endpoint."""
        if not all([self.__session, self.__premise, self.__partner, self.__contract]):
            LOGGER.error("Session or meter parameters not initialized")
            return []
        # Build the endpoint using only supported APIs
        date_str = target_date.strftime("%Y-%m-%d")

        if is_granular:
            # hourly-usage is the only granular endpoint we support
            endpoint = f"/MeterInsight/{self.__partner}/{self.__contract}/{self.__premise}/hourly-usage?date={date_str}"
        else:
            # usage-daily supports start/end; request a single-day range for the target_date
            endpoint = f"/MeterInsight/{self.__partner}/{self.__contract}/{self.__premise}/usage-daily?start={date_str}&end={date_str}"

        try:
            res = self.__session.get(f"https://youraccountonline.electricireland.ie{endpoint}", allow_redirects=True)
            res.raise_for_status()

            data = res.json()

            # Extract datapoints from envelope or direct list
            if isinstance(data, dict) and "data" in data:
                datapoints = data.get("data") or []
            elif isinstance(data, list):
                datapoints = data
            else:
                LOGGER.warning(f"Unexpected response format: {type(data)}")
                return []

            if not datapoints:
                LOGGER.debug(f"No data available for {target_date.strftime('%Y-%m-%d')}")
                return []

            # Normalize to {consumption, cost, intervalEnd}
            normalized = []
            for point in datapoints:
                if not isinstance(point, dict):
                    continue

                # Many endpoints use a flatRate structure
                flat_rate = point.get("flatRate") or point.get("flatrate")
                if flat_rate:
                    consumption = flat_rate.get("consumption", 0)
                    cost = flat_rate.get("cost", 0)
                else:
                    consumption = point.get("consumption", 0)
                    cost = point.get("cost", 0)

                # Determine interval end timestamp if available
                end_date = point.get("endDate") or point.get("end_date") or point.get("intervalEnd")
                interval_end = None
                if end_date:
                    try:
                        dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                        interval_end = int(dt.timestamp())
                    except Exception:
                        try:
                            interval_end = int(float(end_date))
                        except Exception:
                            interval_end = None

                # Skip datapoints without a valid interval end to avoid downstream errors
                if interval_end is None:
                    continue

                normalized.append({"consumption": consumption, "cost": cost, "intervalEnd": interval_end})

            LOGGER.debug(f"Normalized {len(normalized)} datapoints for {target_date.strftime('%Y-%m-%d')}")
            return normalized

        except RequestException as err:
            LOGGER.error(f"Failed to get data for {target_date.strftime('%Y-%m-%d')}: {err}")
            return []
        except Exception as e:
            LOGGER.error(f"Unexpected error getting data for {target_date.strftime('%Y-%m-%d')}: {e}")
            return []

    def get_bill_projection(self):
        """Call the bill-projection endpoint and return the projection data dict (or None)."""
        if not all([self.__session, self.__premise, self.__partner, self.__contract]):
            LOGGER.error("Session or meter parameters not initialized")
            return None

        endpoint = f"/MeterInsight/{self.__partner}/{self.__contract}/{self.__premise}/bill-projection"
        try:
            res = self.__session.get(f"https://youraccountonline.electricireland.ie{endpoint}", allow_redirects=True)
            res.raise_for_status()
            data = res.json()
            if isinstance(data, dict):
                return data.get("data") or data
            return data
        except RequestException as err:
            LOGGER.error(f"Failed to get bill projection: {err}")
            return None
        except Exception as e:
            LOGGER.error(f"Unexpected error getting bill projection: {e}")
            return None
