package config

import (
	"log"

	"gopkg.in/ini.v1"
)

// Config 应用配置(读取 Config/config.ini)
type Config struct {
	HttpPort   int
	DBType     string
	DBUser     string
	DBPassword string
	DBHost     string
	DBName     string
}

var cfg *Config

// Load 读取配置文件,只执行一次
func Load() {
	if cfg != nil {
		return
	}

	f, err := ini.Load("Config/config.ini")
	if err != nil {
		log.Fatalln("Fial to parse 'Config/config.ini': ", err)
	}

	serverSec, err := f.GetSection("server")
	if err != nil {
		log.Fatalln("Fail to get config section 'server': ", err)
	}

	dbSec, err := f.GetSection("database")
	if err != nil {
		log.Fatalln("Fail to get config section 'database': ", err)
	}

	cfg = &Config{
		HttpPort:   serverSec.Key("HTTP_PORT").MustInt(3000),
		DBType:     dbSec.Key("TYPE").String(),
		DBUser:     dbSec.Key("USER").String(),
		DBPassword: dbSec.Key("PASSWORD").String(),
		DBHost:     dbSec.Key("HOST").String(),
		DBName:     dbSec.Key("NAME").String(),
	}
}

// Get 返回已加载的配置
func Get() *Config {
	return cfg
}
