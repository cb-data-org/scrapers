"""Source adapters. Import each adapter module here so it registers itself (`from . import cardapioweb`)."""

from . import osm_venues, overture_places, precodahora

__all__ = ["osm_venues", "overture_places", "precodahora"]
