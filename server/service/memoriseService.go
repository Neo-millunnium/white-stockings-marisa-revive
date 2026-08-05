package service

import (
	"strings"

	"server/model"
	"server/repository"
	"server/segment"
)

// 业务常量
const (
	// 未命中时的兜底回答
	notFoundAnswer = "唔嗯...不懂你在说什么呢...教教我吧~"
	// 学习合并阈值:与已有词条重合度达到该比例时合并记忆
	mergeThreshold = 0.6
	// 回复命中阈值:与词条重合度达到该比例即命中
	replyThreshold = 0.4
)

// IMemoriseService 记忆业务接口
type IMemoriseService interface {
	// Add 记忆学习
	Add(memory model.Memorise) map[string]interface{}
	// Reply 回复
	Reply(memory model.Memorise) (int, map[string]interface{})
	// Forget 忘记
	Forget(answer string) bool
	// Status 状态
	Status() int
}

// NewMemoriseService 创建记忆服务
func NewMemoriseService(repo repository.IMemoriseRepo) IMemoriseService {
	return &memoriseService{repo: repo}
}

type memoriseService struct {
	repo repository.IMemoriseRepo
}

// Add 教学:对输入关键词分词后入库。
// 若与已有词条重合度 >= mergeThreshold,则将两个分词集合去重合并后再入库;
// 否则直接使用输入分词。原实现内层循环每次迭代都 goto DATA,
// 导致只用第一个 keyword 做合并判断,这里重写为完整遍历 + 去重合并。
func (m *memoriseService) Add(memory model.Memorise) map[string]interface{} {
	toPpl := segment.Init().Cut(memory.Keyword)
	real := strings.Join(toPpl, ",")

	for _, v := range m.repo.FetchAllMemory() {
		keywords := strings.Split(v.Keyword, ",")
		if overlapRatio(keywords, toPpl) >= mergeThreshold {
			real = strings.Join(mergeUnique(keywords, toPpl), ",")
			break
		}
	}

	data := map[string]interface{}{
		"ip":      memory.Ip,
		"keyword": real,
		"answer":  memory.Answer,
	}
	if m.repo.AddMemory(data) {
		return data
	}
	return nil
}

// Reply 回复:分词后与全部记忆比对,重合度 >= replyThreshold 即命中。
// 命中后直接返回该条记忆的 Answer。
// 原实现命中后用 FetchMemory(answer) 按 answer 反查数据库,可能返回错误/空数据,
// 且 ratio 存在 0 >= 0.4 的恒假分支和无意义循环,这里一并清理。
func (m *memoriseService) Reply(memory model.Memorise) (int, map[string]interface{}) {
	data := make(map[string]interface{})
	toPpl := segment.Init().Cut(memory.Keyword)

	for _, v := range m.repo.FetchAllMemory() {
		keywords := strings.Split(v.Keyword, ",")
		if overlapRatio(keywords, toPpl) >= replyThreshold {
			data["answer"] = v.Answer
			return 200, data
		}
	}

	data["answer"] = notFoundAnswer
	return 10001, data
}

// Forget 按 answer 删除记忆
func (m *memoriseService) Forget(answer string) bool {
	return m.repo.DeleteMemoryByAnswer(answer)
}

// Status 返回当前记忆条数
func (m *memoriseService) Status() int {
	return len(m.repo.FetchAllMemory())
}

// overlapRatio 计算 keywords 中出现在 words 里的词所占比例(重合度)。
// keywords 为空时返回 0,避免除零。
func overlapRatio(keywords, words []string) float64 {
	if len(keywords) == 0 {
		return 0
	}
	matched := 0
	for _, kw := range keywords {
		for _, w := range words {
			if kw == w {
				matched++
				break
			}
		}
	}
	return float64(matched) / float64(len(keywords))
}

// mergeUnique 将两个分词集合去重合并,保持原顺序(先 a 后 b)。
func mergeUnique(a, b []string) []string {
	result := make([]string, 0, len(a)+len(b))
	seen := make(map[string]struct{}, len(a)+len(b))
	for _, s := range append(a, b...) {
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		result = append(result, s)
	}
	return result
}
