// The colours from css/tokens.css, as values the map and chart libraries can use.

const css = getComputedStyle(document.documentElement);
const token = (name) => css.getPropertyValue(`--${name}`).trim();

export const colours = {
  paper: token("paper"),
  ink: token("ink"),
  ink2: token("ink-2"),
  rule: token("rule"),
  straw: token("straw"),
  soil: token("soil"),
  rain: token("rain"),
  alarm: token("alarm"),
};

// crop (params.yaml name) -> the word a farmer uses and its outline colour on the map
export const crops = {
  wheat: { word: "wheat", colour: token("wheat") },
  barley: { word: "barley", colour: token("barley") },
  osr: { word: "OSR", colour: token("osr") },
};
export const cropWord = (crop) => crops[crop]?.word ?? crop;
export const cropColour = (crop) => crops[crop]?.colour ?? colours.paper;

export const dataFont = token("font-data");
