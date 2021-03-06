from feed_process.models.db import DBSession
from feed_process.models import TempAd, TempAdProperty
from sqlalchemy.sql.expression import func
from feed_process import LOG_FOLDER
from feed_process.main_load import get_loader_data_connection
from feed_process.translation import Translator
from feed_process.tools.cleaner import slugify
import importlib
import os
import time
import logging
import datetime as dtt

translator = Translator()

def create_loader(loader_name):
    loader_data_connection = get_loader_data_connection(loader_name)
    return getattr(
            importlib.__import__("feed_process.main_load.loader", fromlist = [loader_name]), 
            loader_name)(**loader_data_connection)

def prepare_ad_data(loader, temp_ad):
    ad_data = {}
    ad_data["data"] = {prop_name: prop.value for prop_name, prop in temp_ad.properties.items()} 
    ad_data["data"]["sitioId"] = temp_ad.id
    ad_data["data"]["sitio"] = temp_ad.feed_in.partner_code
    ad_data["data"]["canonicalUrl"] = set_canonical_url(loader, temp_ad)

    # If feed is reliable then moderated = 0 and enabled = 1 and verfied = 1
    # Else moderated = 1 and enabled = 0 and verified = 0
    ad_data["data"]["moderated"] = int(not temp_ad.feed_in.reliable)
    ad_data["data"]["enabled"] = int(temp_ad.feed_in.reliable)
    ad_data["data"]["verified"] = int(temp_ad.feed_in.reliable)        

    ad_data["images"] = [image.internal_path for image in temp_ad.images]

    return ad_data

def set_canonical_url(loader, temp_ad):

    # Getting location data
    location_id = temp_ad.properties["location_id"].value 
    location_slug = loader.get_location(location_id)["locationslug"]
    
    # Getting country data
    country_id = temp_ad.feed_in.country_id
    country = loader.get_country(country_id)
    domain = country["countrydomain"]
    country_slug = country["countryslug"]

    # Getting subcategory data
    subcategory_id = temp_ad.properties["subcatid"].value
    subcategory_slug = loader.get_subcategory(subcategory_id)["subcatslug"]


    subdomain = location_slug or 'www'

    if (subdomain == 'www.' and country_slug == 'cuba'):
        subdomain = '' # No www for cuba since cuba is cuba.anunico.com
    
    pub_url = 'http://{subdomain}.{domain}/{ad_url}/{subcat_slug}/{title_slug}-{ad_id}.html'.\
                format(
                    subdomain = subdomain,
                    domain = domain,
                    ad_url = translator.translate(temp_ad.feed_in.locale, 'AD_URL', "messages"),
                    subcat_slug = translator.translate(temp_ad.feed_in.locale, subcategory_slug, 'slugs'),
                    title_slug = slugify(temp_ad.properties["adtitle"].value, "_"),
                    ad_id = '%REPLACEID%'
                )

    return pub_url



def run(loader_name, sleep_time = 0):

    log_file_name = os.path.join(
    LOG_FOLDER, 
    "{0}_feeds_load.log".format(dtt.datetime.today().strftime("%Y-%m-%d")))

    # start - logging configuration
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    log_formatter = logging.Formatter('[%(asctime)s] %(message)s')
    logger_handler = logging.FileHandler(log_file_name)
    logger_handler.setFormatter(log_formatter)
    logger.addHandler(logger_handler)
    # end - logging configuration

    loader = create_loader(loader_name)
    
    processed_temp_ads = []
    errors = 0
    loaded_ok = 0

    limit = 100
    
    query = DBSession.query(TempAd).filter(TempAd.is_ready, TempAd.ad_id == None)
    temp_ads = query.order_by(func.rand()).limit(limit).all()
    
    while True:

        processed_temp_ads += [temp_ad.id for temp_ad in temp_ads]

        if not temp_ads:
            logger.info("FINISHED. ads loaded OK: {0}. Errors: {1}".format(loaded_ok, errors))
            break

        ads_data = []


        for temp_ad in temp_ads:
            try:
                ad_data = prepare_ad_data(loader, temp_ad)
                ads_data.append(ad_data)
            except Exception as e:
                temp_ad.error_message = "Error while it is preparing data to load: " + str(e)
                logger.info("{0} {1} {2}".format(temp_ad.id, temp_ad.ad_id, temp_ad.error_message or ""))
                errors += 1

                pass

        # It loads the ads 
        result = loader.load(ads_data)

        for res in result:
            temp_ad = DBSession.query(TempAd).get(res["id"])
            temp_ad.ad_id = res["ad_id"]
            temp_ad.error_message = res["error_message"]

            if res["error_message"]:
                errors += 1
            else:
                loaded_ok += 1 

            logger.info("{0} {1} {2}".format(temp_ad.id, temp_ad.ad_id, temp_ad.error_message or ""))

        DBSession.commit()

        temp_ads = query.filter(~ TempAd.id.in_(processed_temp_ads)).\
                        order_by(func.rand()).\
                        limit(limit).\
                        all()

        # Process sleeps in order to avoid overload API's server.
        time.sleep(sleep_time)

