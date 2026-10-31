"""Configuracion comun de pytest para database/.

ADR-033: importar `api.py` prende `valorizacion.VIVO_HABILITADO` para todo el
proceso. En los tests eso haria que, corridos en horario de rueda, cualquier
valuacion saliera a la red (dolarapi/data912) y diera resultados que cambian
minuto a minuto. Cada test arranca con el vivo apagado (cierre guardado); el
que quiera probar el vivo lo prende y simula las fuentes (test_precios_vivo.py).
"""
import pytest

import valorizacion


@pytest.fixture(autouse=True)
def _valuacion_con_cierre(monkeypatch):
    monkeypatch.setattr(valorizacion, "VIVO_HABILITADO", False)
