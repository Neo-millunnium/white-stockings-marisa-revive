package segment

import (
	"sync"

	"github.com/huichen/sego"
)

// Segmenter sego 中文分词器封装(保留原实现)
type Segmenter struct {
}

var (
	instance  *Segmenter
	once      sync.Once
	segmenter sego.Segmenter
)

// Init 初始化分词器,词典路径固定为 Config/dictionary.txt
func Init() *Segmenter {
	once.Do(func() {
		segmenter.LoadDictionary("Config/dictionary.txt")
		instance = &Segmenter{}
	})
	return instance
}

// Cut 对输入文本做中文分词,返回分词后的词条列表
func (s *Segmenter) Cut(str string) []string {
	origin := []byte(str)
	segments := segmenter.Segment(origin)
	return sego.SegmentsToSlice(segments, true)
}
