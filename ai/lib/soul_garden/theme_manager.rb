require 'yaml'

module SoulGarden
  class ThemeManager
    attr_reader :all_themes, :current_themes

    def initialize(data_path = nil)
      data_path ||= File.expand_path('../../data/themes.yml', __dir__)
      @all_themes = YAML.load_file(data_path) || []
      @current_themes = []
    end

    def extract_new_themes(count = 5)
      # Randomly select `count` themes
      @current_themes = @all_themes.sample(count)
      @current_themes
    end
  end
end
