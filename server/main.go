package main

import (
	"fmt"

	"github.com/gin-gonic/gin"

	"server/Config"
	"server/database"
	"server/handler"
	"server/repository"
	"server/Routes"
	"server/service"
)

func main() {
	// 1. 加载配置(Config/config.ini)
	config.Load()

	// 2. 连接数据库
	db := database.Connect()

	// 3. 组装依赖:repository -> service -> handler
	repo := repository.NewMemoriseRepo(db)
	svc := service.NewMemoriseService(repo)
	h := handler.NewHandler(svc)

	// 4. 创建 gin 引擎(默认带日志与恢复中间件)
	r := gin.Default()

	// 5. 注册路由
	routes.Register(r, h)

	// 6. 启动 HTTP 服务
	if err := r.Run(fmt.Sprintf(":%d", config.Get().HttpPort)); err != nil {
		panic(err)
	}
}
