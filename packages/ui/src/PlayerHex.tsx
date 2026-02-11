import React from "react";

interface PlayerHexProps {
  name: string;
  number: string;
  playerId: number;
  imageUrl: string;
  position: string;
  rating?: number; // 0-100, we'll use this later for the color ring
}

export const PlayerHex = ({ name, number, playerId, imageUrl, position, rating = 85 }: PlayerHexProps) => {
  // Hexagon math: 6 points around a circle
  const hexPoints = "50,0 93.3,25 93.3,75 50,100 6.7,75 6.7,25";

  // Color logic based on rating (Green = Good, Red = Bad)
  const ringColor = rating > 90 ? "#10B981" : rating > 75 ? "#3B82F6" : "#EF4444";

  return (
    <div className="flex flex-col items-center w-[120px] m-2 group cursor-pointer transition-transform hover:scale-105">
      <div className="relative w-[100px] h-[100px]">
        <svg viewBox="0 0 100 100" className="drop-shadow-lg">
          {/* 1. Background Fill */}
          <polygon points={hexPoints} fill="#1e293b" />

          {/* 2. Mask Definition */}
          <defs>
            <clipPath id={`hexClip-${playerId}`}>
              <polygon points={hexPoints} />
            </clipPath>
          </defs>

          {/* 3. Player Image (Draw this BEFORE the border) */}
          <image
            href={imageUrl}
            x="0" y="0" height="100" width="100"
            clipPath={`url(#hexClip-${playerId})`}
            preserveAspectRatio="xMidYMid slice"
            className="opacity-90 group-hover:opacity-100 transition-opacity"
          />

          {/* 4. Rating Ring (Draw this LAST so it sits on top) */}
          <polygon
            points={hexPoints}
            fill="none"
            stroke={ringColor}
            strokeWidth="3"
            className="opacity-80 group-hover:opacity-100 transition-opacity"
          />
        </svg>

        {/* 5. Badge (Handles '00' correctly) */}
        <div className="absolute -bottom-2 left-1/2 transform -translate-x-1/2 bg-slate-800 text-white text-[10px] font-bold px-2 py-0.5 rounded-md border border-slate-600 shadow-sm whitespace-nowrap z-10">
           {number && number !== "00" ? `#${number}` : position}
        </div>
      </div>

      {/* 6. Name (Truncated) */}
      <div className="mt-3 text-center w-full">
        <p className="text-sm font-bold text-slate-200 leading-tight truncate px-1">
          {name}
        </p>
        <p className="text-[10px] text-slate-400 uppercase tracking-wider">
          {position}
        </p>
      </div>
    </div>
  );
};
