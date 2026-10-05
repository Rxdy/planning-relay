"""F3 : un mail par semaine touchée, qui montre la semaine entière.

Une modification en semaine 42 ne renvoie que la semaine 42 ; une semaine qui
vient d'être publiée part en un mail « publiée ». Chaque mail a une version
texte et une version HTML (jours modifiés surlignés, ancien créneau barré).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from html import escape

from .config import Config
from .modeles import Changement, Creneau, poste

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
ETIQUETTES = {"ajout": "Ajouté", "modif": "Modifié", "suppr": "Annulé"}


@dataclass(frozen=True)
class Mail:
    objet: str
    texte: str
    html: str


def par_semaine(changements: list[Changement]) -> dict[date, list[Changement]]:
    semaines: dict[date, list[Changement]] = {}
    for ch in changements:
        semaines.setdefault(ch.jour - timedelta(days=ch.jour.weekday()), []).append(ch)
    return dict(sorted(semaines.items()))


def _periode(lundi: date) -> str:
    dimanche = lundi + timedelta(days=6)
    if lundi.month == dimanche.month:
        return f"{lundi.day}–{dimanche.day} {MOIS[lundi.month - 1]}"
    return f"{lundi.day} {MOIS[lundi.month - 1]} – {dimanche.day} {MOIS[dimanche.month - 1]}"


def _publiee(lundi: date, connus: dict[date, Creneau]) -> bool:
    """L'agenda n'avait encore rien pour cette semaine."""
    return not any(lundi <= j <= lundi + timedelta(days=6) for j in connus)


def objet(cfg: Config, lundi: date, chs: list[Changement], publiee: bool) -> str:
    sem = f"sem. {lundi.isocalendar().week} ({_periode(lundi)})"
    if publiee:
        etat = "publiée"
    else:
        etat = f"{len(chs)} changement{'s' if len(chs) > 1 else ''}"
    return f"Planning de {cfg.personne} · {sem} · {etat}"


def _jour(j: date) -> str:
    return f"{JOURS[j.weekday()]} {j:%d/%m}"


def _nom(c: Creneau) -> str:
    """Matin, Soir ou Nuit ; pour une absence journée entière, son intitulé Silae."""
    p = poste(c)
    return p if p != "Autre" else (c.intitule or c.code)


def _creneau(c: Creneau | None) -> str:
    # Un jour sans créneau est un repos : R et RF ne sont pas reportés.
    if c is None:
        return "Repos"
    if c.journee_entiere:
        return _nom(c)
    return f"{_nom(c)} {c.debut:%H:%M}–{c.fin:%H:%M}"


def _horaires(c: Creneau) -> str:
    if c.journee_entiere:
        return "journée entière"
    lendemain = " (lendemain)" if c.fin_le_lendemain else ""
    return f"{c.debut:%H:%M}–{c.fin:%H:%M}{lendemain}"


def details(ch: Changement) -> list[str]:
    """Ce qui a changé ce jour-là, en phrases courtes."""
    if ch.type == "ajout":
        return [f"Repos → {_creneau(ch.apres)}"]
    if ch.type == "suppr":
        return [f"{_creneau(ch.avant)} → Repos"]
    a, b = ch.avant, ch.apres
    lignes = []
    if _nom(a) != _nom(b):
        lignes.append(f"Poste : {_nom(a)} → {_nom(b)}")
    if (a.debut, a.fin) != (b.debut, b.fin):
        lignes.append(f"Horaires : {_horaires(a)} → {_horaires(b)}")
    if a.duree and b.duree and a.duree != b.duree:
        lignes.append(f"Durée : {a.duree} → {b.duree}")
    if not lignes:  # même poste et mêmes horaires : seul le code Silae a changé
        lignes.append(f"Code Silae : {a.code} → {b.code}")
    return lignes


def texte(cfg: Config, lundi: date, planning: dict[date, Creneau], chs: list[Changement],
          aujourdhui: date, publiee: bool) -> str:
    par_jour = {ch.jour: ch for ch in chs}
    entete = f"Planning de {cfg.personne}, semaine {lundi.isocalendar().week} ({_periode(lundi)})"
    lignes = [entete, "=" * len(entete), ""]
    if publiee:
        lignes += ["Nouvelle semaine publiée.", ""]
    else:
        lignes.append("CE QUI CHANGE")
        for ch in chs:
            lignes.append(f"- {_jour(ch.jour)} · {ETIQUETTES[ch.type]}")
            lignes += [f"    {d}" for d in details(ch)]
        lignes += ["", "LA SEMAINE"]
    for i in range(7):
        j = lundi + timedelta(days=i)
        c = planning.get(j)
        marque = "*" if j in par_jour and not publiee else " "
        if c is None or c.journee_entiere:
            contenu = _creneau(c)
        else:
            contenu = f"{c.debut:%H:%M}–{c.fin:%H:%M}   {_nom(c)}"
        ligne = f"{marque} {_jour(j)}   {contenu}"
        if j < aujourdhui:
            ligne += "   [passé]"
        lignes.append(ligne)
    if not publiee:
        lignes += ["", "* = jour qui a changé"]
    return "\n".join(lignes)


COULEURS = {"ajout": "#1a7f37", "modif": "#a66b00", "suppr": "#b42318"}

# Pas les couleurs de Silae : la nuit y est rouge, comme « Annulé » ici.
POSTES = {"Matin": "#2e9e4f", "Soir": "#ef7d1a", "Nuit": "#3b4cc0", "Autre": "#8a8a8a"}

def _pastille(couleur: str) -> str:
    return (f"<span style='display:inline-block;width:10px;height:10px;border-radius:2px;"
            f"background:{couleur};margin-right:6px;vertical-align:baseline'></span>")


def legende(planning: dict[date, Creneau], lundi: date, aujourdhui: date, publiee: bool) -> str:
    semaine = [planning[j] for j in sorted(planning) if lundi <= j <= lundi + timedelta(days=6)]
    presents = {poste(c) for c in semaine} - {"Autre"}
    postes = "".join(
        f"<span style='margin-right:14px;white-space:nowrap'>{_pastille(POSTES[p])}{p}</span>"
        for p in POSTES if p in presents
    )
    reperes = []
    if not publiee:
        reperes.append("<span style='background:#fff4cc;padding:0 4px'>surligné</span> jour qui a changé")
        reperes.append("<s>barré</s> ancien créneau")
    if lundi < aujourdhui:
        reperes.append("<span style='color:#999'>gris</span> jour passé")
    ligne_reperes = f"<div style='margin-top:6px'>{' · '.join(reperes)}</div>" if reperes else ""
    return (
        "<div style='font-size:13px;color:#555;margin:12px 0 0;line-height:1.6'>"
        f"<div>{postes}</div>{ligne_reperes}</div>"
    )


def html(cfg: Config, lundi: date, planning: dict[date, Creneau], chs: list[Changement],
         aujourdhui: date, publiee: bool) -> str:
    par_jour = {ch.jour: ch for ch in chs}

    resume = ""
    if not publiee:
        items = "".join(
            f"<li style='margin:0 0 8px'><strong>{_jour(ch.jour)}</strong> "
            f"<span style='color:{COULEURS[ch.type]};font-size:13px;font-weight:600'>{ETIQUETTES[ch.type]}</span>"
            + "".join(f"<br><span style='font-size:14px'>{escape(d)}</span>" for d in details(ch))
            + "</li>"
            for ch in chs
        )
        resume = (
            "<div style='background:#fff8e1;border-left:4px solid #f0b400;padding:12px 16px;margin:0 0 16px'>"
            "<div style='font-size:13px;font-weight:700;letter-spacing:.04em;color:#7a5600;margin:0 0 8px'>CE QUI CHANGE</div>"
            f"<ul style='margin:0;padding-left:18px'>{items}</ul></div>"
        )

    lignes = []
    for i in range(7):
        j = lundi + timedelta(days=i)
        c, ch = planning.get(j), par_jour.get(j)
        surligne = ch is not None and not publiee
        fond = "#fff4cc" if surligne else "transparent"
        couleur = "#999" if j < aujourdhui else "#222"
        # Carré de couleur et horaires sur une ligne, le poste en gris dessous
        if c is None:
            creneau = "<span style='color:#999;margin-left:16px'>Repos</span>"
        elif c.journee_entiere:
            creneau = f"{_pastille(POSTES['Autre'])}<strong>{escape(_nom(c))}</strong>"
        else:
            creneau = (f"{_pastille(POSTES[poste(c)])}<strong>{c.debut:%H:%M}–{c.fin:%H:%M}</strong>"
                       f"<br><span style='color:#666;font-size:13px;margin-left:16px'>{escape(_nom(c))}</span>")
        if surligne and ch.avant:
            creneau += f"<br><span style='color:#b42318;font-size:13px;margin-left:16px'>avant : <s>{escape(_creneau(ch.avant))}</s></span>"
        etiquette = (
            f"<span style='color:{COULEURS[ch.type]};font-size:12px;font-weight:600'>{ETIQUETTES[ch.type]}</span>"
            if surligne else ""
        )
        lignes.append(
            f"<tr style='background:{fond};color:{couleur};border-top:1px solid #eee'>"
            f"<td style='padding:8px 12px;white-space:nowrap;vertical-align:top'>{_jour(j)}</td>"
            f"<td style='padding:8px 12px'>{creneau}</td>"
            f"<td style='padding:8px 12px;vertical-align:top;text-align:right'>{etiquette}</td></tr>"
        )
    titre = f"Semaine {lundi.isocalendar().week} · {_periode(lundi)}"
    sous_titre = "Nouvelle semaine publiée." if publiee else f"{len(chs)} jour{'s' if len(chs) > 1 else ''} modifié{'s' if len(chs) > 1 else ''}."
    return (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;color:#222;max-width:520px'>"
        f"<h2 style='font-size:18px;margin:0 0 4px'>{escape(titre)}</h2>"
        f"<p style='margin:0 0 12px;color:#666'>{sous_titre}</p>"
        + resume
        + "<table style='border-collapse:collapse;border:1px solid #e5e5e5;width:100%'>"
        + "".join(lignes)
        + "</table>"
        + legende(planning, lundi, aujourdhui, publiee)
        + "</div>"
    )


def mails(cfg: Config, planning: dict[date, Creneau], connus: dict[date, Creneau],
          changements: list[Changement], aujourdhui: date) -> list[Mail]:
    """planning : la plateforme depuis lundi ; connus : l'agenda avant ce passage."""
    resultat = []
    for lundi, chs in par_semaine(changements).items():
        publiee = _publiee(lundi, connus)
        resultat.append(Mail(
            objet(cfg, lundi, chs, publiee),
            texte(cfg, lundi, planning, chs, aujourdhui, publiee),
            html(cfg, lundi, planning, chs, aujourdhui, publiee),
        ))
    return resultat


class Messagerie:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def envoyer(self, sujet: str, texte: str, html: str | None = None) -> None:
        msg = EmailMessage()
        msg["From"] = self.cfg.smtp_user
        msg["To"] = ", ".join(self.cfg.mail_to)
        msg["Subject"] = sujet
        msg.set_content(texte)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as smtp:
            smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
            smtp.send_message(msg)

    def par_semaine(self, planning: dict[date, Creneau], connus: dict[date, Creneau],
                    changements: list[Changement], aujourdhui: date) -> None:
        for m in mails(self.cfg, planning, connus, changements, aujourdhui):
            self.envoyer(m.objet, m.texte, m.html)
