from cnequity.config import load_config
from rqalpha.interface import AbstractMod

from .datasource import QMTDataSource


class CNEquityDataSourceMod(AbstractMod):
    def start_up(self, env, mod_config):
        config = load_config(mod_config.cnequity_config_path)
        env.set_data_source(QMTDataSource(env.config.base, config, env.config.base.end_date))

    def tear_down(self, code, exception=None):
        return None


def load_mod() -> CNEquityDataSourceMod:
    return CNEquityDataSourceMod()
