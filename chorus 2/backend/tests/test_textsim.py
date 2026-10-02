from chorus.textsim import near_duplicate_groups, normalize


def test_normalize_strips_noise_and_keeps_indic_marks():
    assert normalize("Hello @user!! https://t.co/x  WORLD 😡") == "hello world"
    # Devanagari vowel signs are combining marks; they must survive
    assert normalize("किसान भाई!") == "किसान भाई"


def test_near_duplicates_grouped_distinct_texts_not():
    items = [
        ("1", "this government has completely failed the people of this country"),
        ("2", "@pol_a this government has completely failed the people of this country #wakeup3"),
        ("3", "This government has completely failed the people of this country!! 😡"),
        ("4", "the weather in the hills was lovely this weekend and the trains ran on time"),
        ("5", "short"),
    ]
    groups = near_duplicate_groups(items, threshold=0.55, min_len=20)
    assert len(groups) == 1
    assert sorted(groups[0]) == ["1", "2", "3"]


def test_near_duplicates_work_in_hindi():
    a = "सरकार ने किसानों के लिए कुछ नहीं किया यह सच सबको जानना चाहिए"
    groups = near_duplicate_groups([("1", a), ("2", a + " #सच"), ("3", "आज मौसम बहुत अच्छा है और चाय पीने का मन है")],
                                   threshold=0.55, min_len=20)
    assert [sorted(g) for g in groups] == [["1", "2"]]
