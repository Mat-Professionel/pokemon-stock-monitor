import logging

from .cultura_api import result_for
from .profiles import CulturaProfile


class CulturaMonitor(CulturaProfile):
    """API publique Cultura en priorité, moteur HTML en repli."""

    def check(self, product):
        try:
            result = result_for(product, self.session)
            if result is not None:
                return result
        except Exception as exc:
            logging.warning("[CULTURA] API indisponible, fallback HTML: %s", exc)
        return super().check(product)
