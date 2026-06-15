class MultiAgentBrief < Formula
  include Language::Python::Virtualenv

  desc "Source-grounded, audit-ready multi-agent workflow for business briefs"
  homepage "https://github.com/Stahl-G/multi-agent-brief-workflow"
  url "https://github.com/Stahl-G/multi-agent-brief-workflow/archive/refs/tags/v0.8.2.tar.gz"
  sha256 "TODO_REPLACE_AFTER_RELEASE"
  license "MIT"
  head "https://github.com/Stahl-G/multi-agent-brief-workflow.git", branch: "main"

  depends_on "libxml2"
  depends_on "libxslt"
  depends_on "libyaml"
  depends_on "python@3.12"

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/72/94/1a15dd82efb362ac84269196e94cf00f187f7ed21c242792a923cdb1c61f/typing_extensions-4.15.0.tar.gz"
    sha256 "0cea48d173cc12fa28ecabc3b837ea3cf6f38c6d1136f85cbaaf598984861466"
  end

  resource "lxml" do
    url "https://files.pythonhosted.org/packages/05/3b/aab6728cae887456f409b4d75e8a01856e4f04bd510de38052a47768b680/lxml-6.1.1.tar.gz"
    sha256 "ba96ae44888e0185281e937633a743ea90d5a196c6000f82565ebb0580012d40"
  end

  resource "python-docx" do
    url "https://files.pythonhosted.org/packages/a9/f7/eddfe33871520adab45aaa1a71f0402a2252050c14c7e3009446c8f4701c/python_docx-1.2.0.tar.gz"
    sha256 "7bc9d7b7d8a69c9c02ca09216118c86552704edc23bac179283f2e38f86220ce"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match(/\d+\.\d+\.\d+/, shell_output("#{bin}/multi-agent-brief version"))
  end
end
