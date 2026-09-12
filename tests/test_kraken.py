import pytest
from kraken import KrakenClient, KrakenError


def test_api_error_and_missing_result_are_rejected():
    with pytest.raises(KrakenError): KrakenClient._spot_result({'error':['EGeneral:Invalid arguments']})
    with pytest.raises(KrakenError): KrakenClient._spot_result({'error':[]})
    with pytest.raises(KrakenError): KrakenClient._single_market_result({'last':'cursor'})
