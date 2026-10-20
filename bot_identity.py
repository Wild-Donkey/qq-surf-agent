"""Application-owned public identity shared by onboarding and conversational workers."""
REPOSITORY_URL='https://github.com/Wild-Donkey/qq-surf-agent'

def introduction(model):
    return ('大家好，我是 Luna，一个公开身份的 AI 群友，现在用 '+str(model.get('model','未知模型'))+
            '。喜欢搬屎、接梗聊天，打扰了可以直接叫我安静。源码：'+REPOSITORY_URL)
