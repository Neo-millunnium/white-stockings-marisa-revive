package repository

import (
	"github.com/jinzhu/gorm"

	"server/model"
)

// IMemoriseRepo 记忆数据访问接口
type IMemoriseRepo interface {
	// AddMemory 插入一条记忆
	AddMemory(data map[string]interface{}) bool
	// FetchAllMemory 读取全部记忆
	FetchAllMemory() (memorise []model.Memorise)
	// DeleteMemoryByAnswer 按 answer 删除记忆
	DeleteMemoryByAnswer(answer string) bool
}

// NewMemoriseRepo 创建基于 gorm 的记忆仓库
func NewMemoriseRepo(source *gorm.DB) IMemoriseRepo {
	return &memoriseRepo{source: source}
}

type memoriseRepo struct {
	source *gorm.DB
}

func (m *memoriseRepo) AddMemory(data map[string]interface{}) bool {
	memory := model.Memorise{
		Ip:      data["ip"].(string),
		Keyword: data["keyword"].(string),
		Answer:  data["answer"].(string),
	}
	// 注意:必须传指针,gorm 需要把自增主键写回结构体;
	// 传值会触发 ErrUnaddressable 导致整个事务回滚,插入不生效
	if err := m.source.Create(&memory).Error; err != nil {
		return false
	}
	return true
}

func (m *memoriseRepo) FetchAllMemory() (memorise []model.Memorise) {
	m.source.Find(&memorise)
	return
}

func (m *memoriseRepo) DeleteMemoryByAnswer(answer string) bool {
	if err := m.source.Where("answer = ?", answer).Delete(&model.Memorise{}).Error; err != nil {
		return false
	}
	return true
}
