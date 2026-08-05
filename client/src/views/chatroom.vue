<template>
  <div class="chatroom">
    <div class="container">
      <!-- 左侧:对话区 -->
      <div class="talk-panel">
        <span>うるさい! うるさい.. うるさい...</span>
        <div ref="talkPlace" class="talk-place">
          <div
            v-for="(item, index) in talkList"
            :key="index"
            class="talk_entry"
            :class="{ you_color: item.name === 'You' }"
          >
            <span class="talk_item" :class="{ you_color: item.name === 'You' }">{{ item.name }}</span>&nbsp;:&nbsp;
            <span class="talk_item" :class="{ you_color: item.name === 'You' }" v-text="item.content"></span>
          </div>
        </div>
        <div class="speak">
          <input ref="youInput" v-model="inputText" @keydown.enter="sendMessage" type="text" name="you" />
          <input @click="sendMessage" type="submit" value="发送" />
        </div>
      </div>
      <!-- 右侧:角色立绘 + 系统指令说明 -->
      <div class="profile">
        <div class="avatar"></div>
        <div class="cmd">
          <span class="system-cmd">系统级指令快速说明——</span>
          <span class="system-cmd cmd-collect">
            <span class="marisa-cmd">teach</span>&nbsp;进入内容教学模式
          </span>
          <span class="system-cmd cmd-collect">
            <span class="marisa-cmd">forget</span>&nbsp;忘记最后所说的内容
          </span>
          <span class="system-cmd cmd-collect">
            <span class="marisa-cmd">status</span>&nbsp;查看目前知识所掌握情况
          </span>
          <div class="cmd_desc">
            另外你也可以通过输入
            <span style="font-weight: bold">hint</span> 查看其他人自定义的内容提示或小小线索
            <div class="cmd_desc_content">
              魔理沙无条件的相信你..她把你交给她的所有知识视作珍宝并会很认真的将其牢牢记住..不要让她学坏哦!
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Core, type TalkItem } from '../core'

const MARISA = '白絲魔理沙'
const YOU = 'You'

// 对话记录列表
const talkList = ref<TalkItem[]>([])
// 输入框内容(v-model 双向绑定)
const inputText = ref('')
// 模板引用
const youInput = ref<HTMLInputElement | null>(null)
const talkPlace = ref<HTMLDivElement | null>(null)

// 教学模式标志:0 = 普通对话,1 = 教学中
let cmdFlag = 0
// 教学计数:第 1 次输入关键词,第 2 次输入回答
let teachFlag = 0
// 教学过程中收集的关键词 / 回答
let teachContent: string[] = []

/**
 * 发送消息:回车或点击发送按钮触发。
 * 空内容时魔理沙会吐槽一句;否则先记下 You 的话,再根据当前模式处理。
 */
async function sendMessage() {
  const content = inputText.value.trim()
  if (content === '') {
    talkList.value.push(Core.speak(MARISA, 'ん？ 你说了什么咩 ¿'))
    return
  }

  talkList.value.push(Core.speak(YOU, content))
  if (cmdFlag === 0) {
    await marisaThinking(content)
  } else {
    await teachMarisa(content)
  }
  inputText.value = ''
}

/** 普通对话模式:识别 teach / forget / status / hint 指令,否则交给魔理沙回复 */
async function marisaThinking(content: string) {
  switch (content) {
    case 'teach':
      talkList.value.push(Core.speak(MARISA, '要教给魔里沙什么 ..? 现在只能学习语句.. 如"问和答" .. 中止教学输入 exit ..'))
      talkList.value.push(Core.speak(MARISA, '（ < ゝω·）教学模式启动 ！'))
      cmdFlag = 1
      break
    case 'forget':
      await marisaForget()
      break
    case 'status':
      await marisaStatus()
      break
    case 'hint':
      await marisaHint()
      break
    default:
      await marisaReply(content)
  }
}

/** 教学模式:第一步输入关键词,第二步输入回答(内部用反引号拼成 关键词`回答) */
async function teachMarisa(content: string) {
  // 这些指令都会中止教学
  if (content === 'exit' || content === 'teach' || content === 'forget' || content === 'status') {
    talkList.value.push(Core.speak(YOU, '白絲魔理沙，退出学习模式'))
    cmdFlag = 0
    return
  }

  if (teachFlag === 0) {
    talkList.value.push(Core.speak(MARISA, '那么 ... 在这样的情况下该如何回答呢 ..?'))
  }

  teachFlag++
  teachContent.push(content)

  // 收集到"关键词 + 回答"后,交给教学逻辑
  if (teachFlag > 1) {
    const query = teachContent.join('`')
    const ok = await Core.teach(query)
    talkList.value.push(Core.speak(MARISA, ok ? '行，我知道了' : '魔理沙不想记住 . . . . . . 对不起'))
    cmdFlag = 0
    teachFlag = 0
    teachContent = []
  }
}

/** 向魔理沙提问 */
async function marisaReply(content: string) {
  const answer = await Core.reply(content)
  talkList.value.push(Core.speak(MARISA, answer ?? '唔嗯 ...  不懂你在说什么呢 ...  教教我吧 ..'))
}

/** 忘记最后一次所说的内容 */
async function marisaForget() {
  const ok = await Core.forget(talkList.value)
  talkList.value.push(
    Core.speak(MARISA, ok ? '这句话魔理沙说错了么 ... 呜呜呜对不起 ...' : '魔理沙这阵子不太想忘记东西的样子 ..'),
  )
}

/** 查看当前知识条数 */
async function marisaStatus() {
  const weight = await Core.status()
  if (weight) {
    talkList.value.push(
      Core.speak(MARISA, `目前魔理沙的脑重量是 ${weight} 克。如果我现在还不能理解您的意思的话，请教给我更多的知识，我会非常非常用心学习的～`),
    )
  } else {
    talkList.value.push(Core.speak(MARISA, '我的记忆要一片混乱了 ...'))
  }
}

/** 查看提示线索:随机展示一条已审核通过的记忆 */
async function marisaHint() {
  const hint = await Core.hint()
  if (hint) {
    talkList.value.push(
      Core.speak(MARISA, `给你个小小线索：试试「${hint.keyword}」—— 有人教过：${hint.answer}`),
    )
  } else {
    talkList.value.push(Core.speak(MARISA, '唔 ... 现在还没有任何线索可以给你，快教教我点什么吧 ~'))
  }
}

/** 滚动对话区到底部 */
function scrollBottom() {
  const el = talkPlace.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

// 对话列表变化后(DOM 更新完)自动滚到底部
watch(
  () => talkList.value.length,
  () => scrollBottom(),
  { flush: 'post' },
)

onMounted(() => {
  // 初始自动聚焦输入框(还原旧版 v-focus 指令效果)
  youInput.value?.focus()
})
</script>

<style lang="scss" scoped>
@use '../assets/css/variables' as *;

.chatroom {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 712px;
  height: 512px;
  background: $box-color;
  border: 1px solid $border-color;
}

.container {
  display: flex;
  justify-content: space-between;
  margin: 0 auto;
  margin-top: 6px;
  width: 700px;
  height: 502px;
}

.talk-panel {
  width: 470px;
  height: 500px;
  background: $container-color;
  border: 1px solid $border-color;

  > span {
    display: block;
    width: 100%;
    height: 40px;
    background: $title-color;
    border-bottom: 1px solid $border-color;
    font-size: 14px;
    line-height: 40px;
    text-indent: 9px;
    color: $order-blue;
  }

  .talk-place {
    width: calc(100% - 10px);
    height: 420px;
    padding-left: 10px;
    overflow-y: scroll;

    .talk_entry {
      width: 100%;
      font-size: 14px;
      margin-top: 12px;
      word-break: break-all;
      line-height: 25px;

      .talk_item {
        display: inline;
        background: transparent;
        border: none;
        line-height: 0;
      }
    }
  }

  .speak {
    width: 100%;
    height: 39px;

    input {
      background: none;
      outline: 0;
      border: none;
    }

    & input[name='you'] {
      position: relative;
      top: 10px;
      left: 5px;
      width: 382px;
      height: 24px;
      padding-left: 5px;
      background: #ffffff;
      border: 1px solid $border-color;
      transition: all 0.2s;

      &:hover, &:focus {
        background: #ffffff;
        border-radius: 5px;
      }
    }

    & input[type='submit'] {
      position: relative;
      top: 9px;
      left: 15px;
      width: 48px;
      height: 22px;
      background: #ffffff;
      border: 1px solid $border-color;
      border-radius: 5px;
      font-size: 12px;
      transition: all 0.2s;
    }
  }
}

.profile {
  width: 220px;
  height: 502px;

  .avatar {
    width: 218px;
    height: 250px;
    border: 1px solid $border-color;
    background-image: url('./img/marisa.jpg');
    background-size: 333px;
    background-repeat: no-repeat;
    background-position: 58% 2%;
    margin-bottom: 8px;
  }

  .cmd {
    width: 202px;
    height: 232px;
    padding: 4px 8px;
    background: $container-color;
    border: 1px solid $border-color;
    color: $order-blue;
    font-size: 12px;

    .system-cmd {
      display: block;
      width: 100%;
      margin-bottom: 13px;
      margin-top: 13px;
    }

    .cmd-collect {
      text-indent: 1em;
    }

    .marisa-cmd {
      display: inline;
      font-weight: bold;
      font-family: sans-serif;
      font-size: 13px;
    }

    .cmd_desc {
      position: relative;
      top: -2px;
      line-height: 15px;

      .cmd_desc_content {
        margin-top: 10px;
        line-height: 15px;
      }
    }
  }
}

.you_color {
  // 旧版注释: color: #836FFF !important
  color: #4876FF !important;
}
</style>
