"""Source adapters. Import each adapter module here so it registers itself (`from . import cardapioweb`)."""

from . import instagram_bd, osm_venues, overture_places, precodahora, venue_classify

__all__ = ["instagram_bd", "osm_venues", "overture_places", "precodahora", "venue_classify"]
