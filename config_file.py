import configparser
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')
main_config = config['Main']


