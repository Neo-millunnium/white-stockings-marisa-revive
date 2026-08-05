package handler

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"server/model"
	"server/service"
)

// ModelAndView 统一响应结构。
// Code 200 表示成功,10001 是业务上"没答上"但 HTTP 状态码仍是 200。
type ModelAndView struct {
	Code int         `json:"code"`
	Data interface{} `json:"data"`
}

// Handler 聚合 handler 层依赖
type Handler struct {
	Svc service.IMemoriseService
}

// NewHandler 创建 HTTP 处理器
func NewHandler(svc service.IMemoriseService) *Handler {
	return &Handler{Svc: svc}
}

// Index 首页探活
func (h *Handler) Index(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"code":    200,
		"message": "hello Marisa~",
	})
}

// Add 教学:接收 form 参数 ip/keyword/answer,分词合并后入库
func (h *Handler) Add(c *gin.Context) {
	memory := model.Memorise{}
	if err := c.ShouldBind(&memory); err != nil {
		c.JSON(http.StatusOK, ModelAndView{Code: http.StatusBadRequest, Data: err.Error()})
		return
	}

	if data := h.Svc.Add(memory); data != nil {
		c.JSON(http.StatusOK, ModelAndView{Code: http.StatusOK, Data: data})
		return
	}
	c.JSON(http.StatusOK, ModelAndView{Code: http.StatusBadGateway, Data: "服务器繁忙"})
}

// Reply 回复:接收 form 参数 keyword,返回匹配的回答
func (h *Handler) Reply(c *gin.Context) {
	memory := model.Memorise{}
	if err := c.ShouldBind(&memory); err != nil {
		c.JSON(http.StatusOK, ModelAndView{Code: http.StatusBadRequest, Data: err.Error()})
		return
	}

	code, data := h.Svc.Reply(memory)
	c.JSON(http.StatusOK, ModelAndView{Code: code, Data: data})
}

// Forget 忘记:接收 form 参数 answer,按 answer 删除
func (h *Handler) Forget(c *gin.Context) {
	memory := model.Memorise{}
	if err := c.ShouldBind(&memory); err != nil {
		c.JSON(http.StatusOK, ModelAndView{Code: http.StatusBadRequest, Data: err.Error()})
		return
	}

	if flag := h.Svc.Forget(memory.Answer); flag {
		c.JSON(http.StatusOK, ModelAndView{Code: http.StatusOK, Data: "success"})
		return
	}
	c.JSON(http.StatusOK, ModelAndView{Code: http.StatusBadGateway, Data: "服务器繁忙"})
}

// Status 状态:返回当前记忆条数
func (h *Handler) Status(c *gin.Context) {
	count := h.Svc.Status()
	c.JSON(http.StatusOK, ModelAndView{Code: http.StatusOK, Data: count})
}
