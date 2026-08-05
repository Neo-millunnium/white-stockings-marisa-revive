package model

// Memorise 记忆实体,对应数据库表 memorise
// 注意:gorm 配置为 SingularTable,表名固定为 memorise
type Memorise struct {
	MemoryId int    `gorm:"column:memoryId;primary_key" json:"memoryId"`
	Ip       string `gorm:"column:ip" form:"ip" json:"ip"`
	Keyword  string `gorm:"column:keyword" form:"keyword" json:"keyword"`
	Answer   string `gorm:"column:answer" form:"answer" json:"answer"`
}
