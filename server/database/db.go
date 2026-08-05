package database

import (
	"fmt"
	"log"

	_ "github.com/go-sql-driver/mysql"
	"github.com/jinzhu/gorm"

	"server/Config"
)

var db *gorm.DB

// Connect 建立 MySQL 连接并初始化 gorm,只执行一次
func Connect() *gorm.DB {
	if db != nil {
		return db
	}

	cfg := config.Get()
	dsn := fmt.Sprintf("%s:%s@tcp(%s)/%s?charset=utf8&parseTime=True&loc=Local",
		cfg.DBUser, cfg.DBPassword, cfg.DBHost, cfg.DBName)

	var err error
	db, err = gorm.Open(cfg.DBType, dsn)
	if err != nil {
		log.Fatalln("connecting mysql error: ", err)
	}

	// 与旧实现保持一致:表名使用单数(memorise)
	db.SingularTable(true)
	db.DB().SetMaxIdleConns(10)
	db.DB().SetMaxOpenConns(100)

	return db
}

// GetDB 返回全局数据库连接
func GetDB() *gorm.DB {
	return db
}
