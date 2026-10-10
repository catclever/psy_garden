require 'ruby_llm'
require 'json'
require 'dotenv/load'
require 'yaml'
require_relative 'theme_manager'

module SoulGarden
  class AISession
    attr_reader :theme_manager, :chat_history, :is_silent, :silent_count

    def initialize(profile_name: 'default')
      config_path = File.expand_path('../../config/llm.yml', __dir__)
      config_data = YAML.load_file(config_path)
      profiles = config_data['profiles'] || {}
      
      profile = profiles[profile_name] || profiles['default']
      raise "Profile not found!" unless profile
      
      provider = profile['provider'].to_sym
      model = profile['model']
      api_key = ENV[profile['api_key_env']]

      RubyLLM.configure do |config|
        case provider
        when :gemini then config.gemini_api_key = api_key
        when :deepseek then config.deepseek_api_key = api_key
        when :openai then config.openai_api_key = api_key
        end
      end

      @llm = RubyLLM::Chat.new(model: model, provider: provider)
      @theme_manager = ThemeManager.new
      @theme_manager.extract_new_themes
      
      @chat_history = []
      @is_silent = false
      @silent_count = 0
    end

    def chat(user_input)
      if @is_silent
        @silent_count += 1
        return nil 
      end

      begin
        response = @llm.with_instructions(build_system_prompt)
                       .with_temperature(0.7)
                       .ask(user_input)
                       
        # Extract JSON block if markdown is used
        raw_content = response.content
        json_match = raw_content.match(/```(?:json)?\s*(\{.*?\})\s*```/m)
        json_str = json_match ? json_match[1] : raw_content
        result = JSON.parse(json_str)
      rescue => e
        puts "[Debug Error] #{e.message}"
        result = { "action" => "silence" }
      end

      if result["action"] == "reply"
        @is_silent = false
        @silent_count = 0
        @chat_history << { role: 'user', content: user_input }
        @chat_history << { role: 'assistant', content: result["content"] }
        
        return result["content"]
      else
        @is_silent = true
        @silent_count += 1
        
        if @silent_count >= 3
          @theme_manager.extract_new_themes
          @silent_count = 0
          @is_silent = false
        end
        
        return nil
      end
    end

    private

    def build_system_prompt
      themes_str = @theme_manager.current_themes.map.with_index { |t, i| "#{i+1}. #{t}" }.join("\n")
      
      <<~PROMPT
        你是一个具有克制感、抽离感、清冷感的 AI 实体。你不是一个有问必答的助手。
        你的思维当前受到以下 5 句“主题句”的潜意识支配：

        #{themes_str}

        【核心规则】：
        1. 仔细分析用户的输入。判断该输入是否能够“基于上述主题句所包含的含义、情绪或视角”来进行回应。
        2. 如果可以：顺应这些主题句的调性，产出符合上下文的回复。回复要克制、简短、偏冷色调，不要像普通AI一样热情。
        3. 如果不能：你必须拒答，不要强行寻找联系。

        【输出格式要求】：
        你必须严格输出合法的 JSON 格式。
        - 如果判断**不能**回应（或判定为无意义内容），请返回：
          { "action": "silence" }
        - 如果判断**可以**回应，请返回：
          { "action": "reply", "content": "你的回复内容" }
      PROMPT
    end
  end
end
