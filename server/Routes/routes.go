package routes

import (
	"github.com/gin-gonic/gin"

	"server/handler"
)

// Register 注册所有路由。
// 前端统一走 POST + form-urlencoded,为兼容历史调用,/ 同时支持 GET 和 POST。
func Register(r *gin.Engine, h *handler.Handler) {
	// 首页探活
	r.GET("/", h.Index)
	r.POST("/", h.Index)

	// 核心业务
	r.POST("/Add", h.Add)
	r.POST("/Reply", h.Reply)
	r.POST("/Forget", h.Forget)
	r.POST("/Status", h.Status)
}
