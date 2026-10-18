import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import BackgroundLanguage from "../i18n/BackgroundLanguage";
import i18n from "../i18n";
import { splitPrompt } from "../draft";
import { json, mockFetch } from "./testUtils";

afterEach(() => { vi.restoreAllMocks(); });

it("loads and saves the shared language without changing the interface language",async()=>{
  const fetch=mockFetch([
    [(url,options)=>url==='/api/settings/language' && options.method==='PUT',(_url,options)=>{
      expect(JSON.parse(options.body)).toEqual({language:'en'}); return {language:'en'};
    }],['/api/settings/language',{language:'it'}],
  ]);
  render(<BackgroundLanguage/>);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Lingua di notifiche e messaggi'}));
  const select=await screen.findByLabelText('Lingua dei processi in background');
  fireEvent.change(select,{target:{value:'en'}});fireEvent.click(screen.getByRole('button',{name:'Salva'}));
  await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('Salvata'));
  expect(i18n.resolvedLanguage).toBe('it');
  await act(async()=>{await i18n.changeLanguage('en');});
  expect(screen.getByLabelText('Background language')).toHaveValue('en');
  expect(screen.getByRole('status')).toHaveTextContent('Saved.');
});

it("keeps the selected language after save failure and retries",async()=>{
  let fail=true;
  mockFetch([
    [(url,options)=>options.method==='PUT',()=>fail?json({detail:'private diagnostic'},403):json({language:'en'})],
    ['/api/settings/language',{language:'it'}],
  ]);
  render(<BackgroundLanguage/>);
  fireEvent.click(screen.getByRole('button',{name:'Lingua di notifiche e messaggi'}));
  fireEvent.change(await screen.findByLabelText('Lingua dei processi in background'),{target:{value:'en'}});
  fireEvent.click(screen.getByRole('button',{name:'Salva'}));
  expect(await screen.findByRole('alert')).not.toHaveTextContent('private diagnostic');
  expect(screen.getByLabelText('Lingua dei processi in background')).toHaveValue('en');
  fail=false;fireEvent.click(screen.getByRole('button',{name:'Salva'}));
  await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('Salvata'));
});

it("reports a load failure and allows closing",async()=>{
  mockFetch([['/api/settings/language',()=>json({},401)]]);
  render(<BackgroundLanguage/>);fireEvent.click(screen.getByRole('button',{name:'Lingua di notifiche e messaggi'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Impossibile caricare');
  fireEvent.click(screen.getByRole('button',{name:'Chiudi'}));expect(screen.queryByRole('dialog')).toBeNull();
});

it.each(['Leggi gli allegati che ti ho inviato.\n\nAllegati: foto.png','Read the attachments I sent you.\n\nAttachments: foto.png'])("reconstructs existing attachment-only messages: %s",prompt=>{
  expect(splitPrompt(prompt)).toEqual({text:'',mentions:[],files:['foto.png']});
});
