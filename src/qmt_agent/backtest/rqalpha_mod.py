from rqalpha.interface import AbstractMod

from .datasource import QMTDataSource


class CNEquityDataSourceMod(AbstractMod):
    def start_up(self, env, mod_config):
        env.set_data_source(QMTDataSource(env.config.base))

    def tear_down(self, code, exception=None):
        return None


def load_mod() -> CNEquityDataSourceMod:
    return CNEquityDataSourceMod()
