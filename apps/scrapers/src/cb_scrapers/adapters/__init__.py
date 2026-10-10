"""Source adapters. Import each adapter module here so it registers itself (`from . import cardapioweb`)."""

from . import osm_venues, precodahora

__all__ = ["osm_venues", "precodahora"]
