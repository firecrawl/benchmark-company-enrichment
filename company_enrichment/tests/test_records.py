"""Field extraction per provider, on small synthetic responses shaped like each provider's documented API answer.

These are not real provider responses (those are the provider's data and never ship); they carry just the fields the
benchmark reads, so a renamed field or a changed path shows up here first.
"""
from company_enrichment.harness.records import answered, record


def test_apollo():
    R = record("apollo", {"organization": {"name": "Acme", "primary_domain": "acme.com", "linkedin_uid": "123",
                                           "linkedin_url": "http://www.linkedin.com/company/acme-co", "city": None, "state": None,
                                           "raw_address": "117 Adams St, Brooklyn, NY 11201", "country": "United States",
                                           "estimated_num_employees": 950, "industry": "computer software", "founded_year": 1998,
                                           "publicly_traded_symbol": "ACME", "annual_revenue": 1.2e9}})
    assert (R["city"], R["state"], R["emp_count"], R["li_slug"], R["li_ids"], R["tickers"]) == \
           ("Brooklyn", "NY", 950, "acme-co", {"123"}, ["ACME"])
    assert all(answered(R, f) for f in ("hq", "industry", "employees", "founded", "linkedin", "ticker", "revenue"))


def test_fullenrich_line2_and_band():
    R = record("fullenrich", {"companies": [{"name": "Acme", "domain": "acme.com", "headcount": 0, "headcount_range": "501-1000",
                                             "industry": {"main_industry": "Software Development"}, "year_founded": 1998,
                                             "social_profiles": {"professional_network": {"url": "https://linkedin.com/company/acme-co", "id": 123}},
                                             "locations": {"headquarters": {"line2": "500 Charles Ewing Boulevard Ewing, New Jersey 08628",
                                                                            "country_code": "US"}}}]})
    assert (R["city"], R["state"], R["emp_count"], R["emp_band"], R["li_ids"]) == ("Ewing", "New Jersey", None, "501-1000", {"123"})
    assert not answered(R, "ticker") and not answered(R, "revenue")


def test_datalegion_premium_has_no_hq():
    R = record("datalegion", {"matches": [{"company": {"name": {"display": "Acme"}, "domain": "acme.com", "linkedin_id": 123,
                                                       "linkedin_url": "linkedin.com/company/acme-co", "size": "501-1000",
                                                       "legion_employee_count": 870, "industry": "software development",
                                                       "founded": 1998, "tickers": [{"symbol": "ACME"}]}}]})
    assert R["name"] == "Acme" and R["emp_count"] == 870 and not answered(R, "hq") and R["tickers"] == ["ACME"]


def test_pdl_unwraps_data_and_falls_back_to_industry_v2():
    R = record("pdl", {"status": 200, "data": {"name": "acme", "display_name": "Acme", "website": "acme.com", "size": "501-1000",
                                               "employee_count": 990, "industry": None, "industry_v2": "software development",
                                               "location": {"locality": "austin", "region": "texas", "country": "united states"},
                                               "linkedin_url": "linkedin.com/company/acme-co", "linkedin_id": "123",
                                               "inferred_revenue": "$1B-$10B", "ticker": "acme", "founded": 1998}})
    assert (R["city"], R["industry"], R["emp_count"], R["tickers"]) == ("austin", "software development", 990, ["acme"])
    assert answered(R, "revenue") and answered(R, "ticker")


def test_ocean_uses_its_own_count_and_primary_location():
    R = record("ocean", {"domain": "acme.com", "name": "Acme", "companySize": "501-1000", "employeeCountOcean": 640,
                         "employeeCountLinkedin": 1010, "linkedinIndustry": "Software Development", "yearFounded": 1998,
                         "revenue": "100-500M", "medias": {"linkedin": {"url": "https://www.linkedin.com/company/acme-co"}},
                         "locations": [{"locality": "Dallas", "region": "TX", "country": "us"},
                                       {"primary": True, "locality": "Greater Austin Area", "region": "TX", "country": "us"}]})
    assert (R["city"], R["emp_count"], R["li_slug"], R["revenue"]) == ("Austin", 640, "acme-co", "100-500M")


def test_contactout_free_text_address():
    R = record("contactout", {"name": "Acme", "domain": "acme.com", "li_vanity": "https://www.linkedin.com/company/acme-co",
                              "headquarter": "8665 E. Hartford Drive, Suite 200 Scottsdale, Arizona 85255, US", "size": 1000,
                              "employees": 870, "industry": "Software Development", "founded_at": 1998, "revenue": 50000000})
    assert (R["city"], R["state"], R["country"], R["emp_count"], R["emp_band"]) == ("Scottsdale", "Arizona", "US", 870, "501-1000")
    R = record("contactout", {"name": "Acme", "headquarter": "101 Spear St, 5th Floor, US"})
    assert not answered(R, "hq")   # an address with no city is no HQ city, not a wrong one
    for addr, city in (("1 Energizer Way, St Louis, Missouri, 63141, US", "St Louis"), ("444 Cedar St, St. Paul, MN, 55101, US", "St. Paul"),
                       ("6100 Tower Cir, Center Valley, PA, 18034, US", "Center Valley")):
        assert record("contactout", {"name": "Acme", "headquarter": addr})["city"] == city, addr   # a city may start with 'St'
    assert record("contactout", {"name": "Acme", "headquarter": "12 Main Street, US"})["city"] is None


def test_external_schema():
    R = record("external", {"company_name": "Acme", "website": "acme.com", "hq_city": "Austin", "hq_state": "TX", "hq_country": "US",
                            "employee_count": 1000, "employee_range": "1001-5000", "industry": "Software Development",
                            "founded_year": 1998, "linkedin_url": "https://www.linkedin.com/company/acme-co",
                            "stock_ticker": "NASDAQ:ACME", "annual_revenue_usd": 1.2e9})
    assert (R["city"], R["state"], R["emp_count"], R["tickers"], R["founded"]) == ("Austin", "TX", 1000, ["ACME"], 1998)


def test_empty_and_failed_answers():
    assert record("apollo", None) is None and record("apollo", {"organization": None}) is None
    assert record("fullenrich", {"companies": []}) is None and not answered(None, "hq")


def test_final_run_audit_address_fixes():
    co = lambda h: record("contactout", {"headquarter": h, "name": "X"})  # noqa: E731
    r = co("100 Example Rd #215, Cape Coral FL, Florida, 33904, US")             # X1
    assert r["city"] == "Cape Coral" and r["state"] == "Florida"
    r = co("First Floor West, 5220 Valiant Court, Gloucester Business Park, Gloucester , GL3 4FE, GB")  # X2
    assert r["city"] == "Gloucester"
    assert co("100 King St W, Toronto, ON, M5X 1A9, CA")["city"] == "Toronto"
    assert co("Plot 12, Vijayawada, Andhra Pradesh, 520010, IN")["city"] == "Vijayawada"   # the city before the region
    assert co("533 Maryville University Dr, St Louis, Missouri, 63141, US")["city"] == "St Louis"
    fe = lambda l2: record("fullenrich", {"companies": [{"name": "X", "locations": {"headquarters": {"line2": l2}}}]})  # noqa: E731
    assert fe("Cape Coral FL, Florida 33904, US")["city"] == "Cape Coral"                    # X3
    assert fe("Gloucester , GL3 4FE, GB")["city"] == "Gloucester"
    ap = record("apollo", {"organization": {"name": "X", "raw_address": "51 Lime Street, London, England EC3M 7DQ, GB"}})  # X4
    assert ap["city"] == "London"
